"""SEC EDGAR HTTP transport.

One client is the fair-access gate for EDGAR. It sends the configured
User-Agent, stays at or under 5 requests per second, and talks only to
``www.sec.gov``, ``data.sec.gov``, and ``efts.sec.gov`` over HTTPS on port 443.
It does not follow redirects, does not read proxy environment variables, and
does not retry HTTP 403.

Retryable failures are HTTP 429, HTTP 5xx, timeouts, and other transient
network errors. The wait before each retry is bounded exponential backoff,
raised to the ``Retry-After`` value when SEC sends one. A ``Retry-After`` above
the configured ceiling is not shortened: the client stops instead of trying
again early.

Filing documents under ``/Archives/edgar/data/`` are immutable and are reused
from the raw cache. Submissions, company facts, and every other SEC URL are
mutable: a cached copy is used only while it is younger than
``sec_mutable_fresh_seconds``. A stale copy is not returned as a success when
the refresh fails.
"""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import NoReturn, Self
from urllib.parse import unquote

import httpx

from titan.common.settings import (
    MAX_SEC_BACKOFF_SECONDS,
    MAX_SEC_MUTABLE_FRESH_SECONDS,
    MAX_SEC_REQUESTS_PER_SECOND,
    MAX_SEC_RESPONSE_BYTES,
    MAX_SEC_RETRIES,
    MAX_SEC_RETRY_AFTER_SECONDS,
    MAX_SEC_TIMEOUT_SECONDS,
    SecSettings,
    get_sec_settings,
    validate_sec_user_agent,
)
from titan.ingest.errors import (
    SecConfigError,
    SecForbiddenError,
    SecHttpStatusError,
    SecRedirectError,
    SecResponseTooLarge,
    SecRetryAfterExceeded,
    SecRetryError,
    SecTransportError,
    SecUrlError,
)
from titan.ingest.raw_cache import RawCacheEntry, RawResponseCache

_ALLOWED_HOSTS = frozenset({"www.sec.gov", "data.sec.gov", "efts.sec.gov"})
_IMMUTABLE_PREFIX = "/Archives/edgar/data/"
_TRANSIENT: tuple[type[httpx.HTTPError], ...] = (
    httpx.TimeoutException,
    httpx.NetworkError,
    httpx.RemoteProtocolError,
)


class EndpointFreshness(StrEnum):
    """Whether a cached SEC response may be reused without a freshness window."""

    IMMUTABLE = "immutable"
    MUTABLE = "mutable"


@dataclass(frozen=True)
class SecResponse:
    """A raw SEC payload, either just fetched or read from the cache."""

    url: str
    body: bytes
    status_code: int
    retrieved_at: datetime
    sha256: str
    from_cache: bool
    freshness: EndpointFreshness
    content_type: str | None


@dataclass(frozen=True)
class _Fetched:
    body: bytes
    content_type: str | None


class _RetryableStatus(Exception):
    def __init__(self, status_code: int, retry_after: float | None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(status_code)


class _RateLimiter:
    """Spaces request starts so a client stays at or under 5 requests/second."""

    def __init__(
        self,
        requests_per_second: float,
        *,
        monotonic: Callable[[], float],
        sleep: Callable[[float], None],
    ) -> None:
        if not 0 < requests_per_second <= MAX_SEC_REQUESTS_PER_SECOND:
            raise SecConfigError(
                "SEC requests are limited to 5 per second and the limit cannot be raised"
            )
        self._interval = 1.0 / requests_per_second
        self._monotonic = monotonic
        self._sleep = sleep
        self._next_allowed: float | None = None
        self._lock = threading.Lock()

    def acquire(self) -> None:
        # Hold the lock across the wait so two callers cannot both start inside
        # the same 1/5 second slot.
        with self._lock:
            now = self._monotonic()
            if self._next_allowed is not None and now < self._next_allowed:
                self._sleep(self._next_allowed - now)
                now = self._monotonic()
            self._next_allowed = now + self._interval


class SecHttpClient:
    """Fetch raw SEC documents with rate limiting, retries, and a disk cache."""

    def __init__(
        self,
        settings: SecSettings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        cache: RawResponseCache | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        resolved = settings if settings is not None else get_sec_settings()
        user_agent = _check_limits(resolved)
        self._settings = resolved
        self._now = _utc_now if clock is None else clock
        self._sleep = time.sleep if sleep is None else sleep
        self._limiter = _RateLimiter(
            resolved.sec_requests_per_second,
            monotonic=time.monotonic if monotonic is None else monotonic,
            sleep=self._sleep,
        )
        self._cache = (
            cache
            if cache is not None
            else RawResponseCache(
                resolved.sec_cache_dir,
                max_bytes=resolved.sec_max_response_bytes,
            )
        )
        self._http = httpx.Client(
            transport=transport,
            timeout=httpx.Timeout(resolved.sec_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": user_agent, "Accept": "*/*"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    @property
    def cache(self) -> RawResponseCache:
        return self._cache

    @property
    def timeout(self) -> httpx.Timeout:
        return self._http.timeout

    @property
    def follow_redirects(self) -> bool:
        return self._http.follow_redirects

    @property
    def trust_env(self) -> bool:
        return self._http.trust_env

    def get(self, url: str) -> SecResponse:
        """Return the raw body for one allowed SEC URL."""
        canonical = canonical_sec_url(url)
        freshness = classify_endpoint(canonical)
        now = self._require_now()
        cached = self._cache.load(canonical)
        if cached is not None and _is_fresh(cached, freshness, now, self._settings):
            return _response_from(cached, freshness, from_cache=True)
        fetched = self._fetch(canonical)
        entry = self._cache.store(
            canonical,
            fetched.body,
            retrieved_at=self._require_now(),
            status_code=200,
            content_type=fetched.content_type,
        )
        return _response_from(entry, freshness, from_cache=False)

    def _require_now(self) -> datetime:
        now = self._now()
        if now.tzinfo is None or now.utcoffset() is None:
            raise SecConfigError("clock must return a timezone-aware datetime")
        return now

    def _fetch(self, url: str) -> _Fetched:
        attempts = self._settings.sec_max_retries + 1
        last_status: int | None = None
        last_error: BaseException | None = None
        performed = 0
        for attempt in range(attempts):
            performed += 1
            self._limiter.acquire()
            retry_after: float | None = None
            try:
                return self._read(url)
            except _RetryableStatus as exc:
                last_status = exc.status_code
                last_error = None
                retry_after = exc.retry_after
            except _TRANSIENT as exc:
                last_status = None
                last_error = exc
            if attempt + 1 == attempts:
                _raise_final(url, performed, last_status, last_error)
            self._wait(url, attempt, retry_after)
        _raise_final(url, performed, last_status, last_error)

    def _read(self, url: str) -> _Fetched:
        with self._http.stream("GET", url) as response:
            status = response.status_code
            if 300 <= status < 400:
                raise SecRedirectError(url, status)
            if status == 403:
                raise SecForbiddenError(url)
            if status == 429 or 500 <= status <= 599:
                raise _RetryableStatus(status, _retry_after(response.headers, self._require_now()))
            if status != 200:
                raise SecHttpStatusError(url, status)
            body = _read_limited(url, response, self._settings.sec_max_response_bytes)
            content_type = response.headers.get("content-type")
            if content_type == "":
                content_type = None
            return _Fetched(body, content_type)

    def _wait(self, url: str, retry_index: int, retry_after: float | None) -> None:
        delay = min(
            self._settings.sec_backoff_base_seconds * (2.0**retry_index),
            self._settings.sec_backoff_max_seconds,
        )
        if retry_after is not None:
            if retry_after > self._settings.sec_retry_after_max_seconds:
                raise SecRetryAfterExceeded(
                    url,
                    retry_after_seconds=retry_after,
                    ceiling=self._settings.sec_retry_after_max_seconds,
                )
            delay = max(delay, retry_after)
        self._sleep(delay)


def canonical_sec_url(url: str) -> str:
    """Return the cache key for an allowed SEC URL, or raise ``SecUrlError``."""
    if not isinstance(url, str) or url == "":
        raise SecUrlError("SEC URL must be a non-empty string")
    if any(character.isspace() or ord(character) < 32 for character in url):
        raise SecUrlError("SEC URL must not contain whitespace or control characters")
    if "\\" in url or "%00" in url.lower():
        raise SecUrlError("SEC URL contains an illegal character")
    try:
        parsed = httpx.URL(url)
    except httpx.InvalidURL as exc:
        raise SecUrlError("SEC URL is invalid") from exc
    if parsed.scheme != "https":
        raise SecUrlError("SEC requests must use HTTPS")
    if parsed.userinfo:
        raise SecUrlError("SEC URL must not include credentials")
    host = parsed.host
    if host not in _ALLOWED_HOSTS:
        raise SecUrlError("host is not an allowed SEC domain")
    if parsed.port not in (None, 443):
        raise SecUrlError("SEC requests must use TCP port 443")
    path = parsed.path or "/"
    if _has_dot_segment(path):
        raise SecUrlError("SEC URL path must not contain dot segments")
    canonical = f"https://{host}{path}"
    if parsed.query:
        try:
            query = parsed.query.decode("ascii")
        except UnicodeDecodeError as exc:
            raise SecUrlError("SEC URL query must be ASCII") from exc
        canonical = f"{canonical}?{query}"
    return canonical


def classify_endpoint(url: str) -> EndpointFreshness:
    """Classify a SEC URL as an immutable archive document or a mutable feed."""
    parsed = httpx.URL(canonical_sec_url(url))
    path = parsed.path
    if (
        parsed.host == "www.sec.gov"
        and path.startswith(_IMMUTABLE_PREFIX)
        and len(path) > len(_IMMUTABLE_PREFIX)
    ):
        return EndpointFreshness.IMMUTABLE
    return EndpointFreshness.MUTABLE


def _has_dot_segment(path: str) -> bool:
    for segment in path.split("/"):
        current = segment
        for _ in range(3):
            if current in {".", ".."}:
                return True
            decoded = unquote(current)
            if decoded == current:
                break
            current = decoded
        if current in {".", ".."}:
            return True
    return False


def _check_limits(settings: SecSettings) -> str:
    try:
        user_agent = validate_sec_user_agent(settings.sec_user_agent)
    except ValueError as exc:
        raise SecConfigError(str(exc)) from exc
    if not 0 < settings.sec_requests_per_second <= MAX_SEC_REQUESTS_PER_SECOND:
        raise SecConfigError(
            "SEC requests are limited to 5 per second and the limit cannot be raised"
        )
    if not 0 < settings.sec_timeout_seconds <= MAX_SEC_TIMEOUT_SECONDS:
        raise SecConfigError("SEC timeout must be greater than 0 and at most 120 seconds")
    if not 0 <= settings.sec_max_retries <= MAX_SEC_RETRIES:
        raise SecConfigError("SEC retries must be between 0 and 5")
    if not 0 < settings.sec_max_response_bytes <= MAX_SEC_RESPONSE_BYTES:
        raise SecConfigError("SEC response size limit cannot be raised past 128 MiB")
    if (
        not 0 < settings.sec_backoff_base_seconds <= MAX_SEC_BACKOFF_SECONDS
        or not 0 < settings.sec_backoff_max_seconds <= MAX_SEC_BACKOFF_SECONDS
        or settings.sec_backoff_max_seconds < settings.sec_backoff_base_seconds
    ):
        raise SecConfigError("SEC backoff must stay inside the configured ceiling")
    if not 0 < settings.sec_retry_after_max_seconds <= MAX_SEC_RETRY_AFTER_SECONDS:
        raise SecConfigError("SEC Retry-After ceiling is invalid")
    if (
        settings.sec_mutable_fresh_seconds < 0
        or settings.sec_mutable_fresh_seconds > MAX_SEC_MUTABLE_FRESH_SECONDS
    ):
        raise SecConfigError("SEC mutable freshness window is invalid")
    return user_agent


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_fresh(
    entry: RawCacheEntry,
    freshness: EndpointFreshness,
    now: datetime,
    settings: SecSettings,
) -> bool:
    if freshness is EndpointFreshness.IMMUTABLE:
        return True
    age = (now - entry.retrieved_at).total_seconds()
    return age < settings.sec_mutable_fresh_seconds


def _response_from(
    entry: RawCacheEntry,
    freshness: EndpointFreshness,
    *,
    from_cache: bool,
) -> SecResponse:
    return SecResponse(
        url=entry.url,
        body=entry.body,
        status_code=entry.status_code,
        retrieved_at=entry.retrieved_at,
        sha256=entry.sha256,
        from_cache=from_cache,
        freshness=freshness,
        content_type=entry.content_type,
    )


def _raise_final(
    url: str,
    attempts: int,
    status_code: int | None,
    cause: BaseException | None,
) -> NoReturn:
    error = SecRetryError(url, attempts=attempts, status_code=status_code)
    if cause is not None:
        raise error from cause
    raise error


def _retry_after(headers: Mapping[str, str], now: datetime) -> float | None:
    raw = headers.get("retry-after")
    if raw is None:
        return None
    return _parse_retry_after(raw, now)


def _parse_retry_after(value: str, now: datetime) -> float | None:
    text = value.strip()
    if text == "":
        return None
    try:
        seconds = float(text)
    except ValueError:
        seconds = None
    if seconds is not None:
        if math.isfinite(seconds) and seconds >= 0:
            return seconds
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    delta = (parsed - now).total_seconds()
    if delta < 0:
        return 0.0
    return delta


def _declared_length(headers: Mapping[str, str]) -> int | None:
    raw = headers.get("content-length")
    if raw is None:
        return None
    text = raw.strip()
    if text == "":
        return None
    if len(text) > 18 or not text.isascii() or not text.isdigit():
        raise SecTransportError("Content-Length is not an integer")
    return int(text)


def _read_limited(url: str, response: httpx.Response, limit: int) -> bytes:
    declared = _declared_length(response.headers)
    if declared is not None and declared > limit:
        raise SecResponseTooLarge(url, declared, limit)
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > limit:
            raise SecResponseTooLarge(url, total, limit)
        chunks.append(chunk)
    return b"".join(chunks)
