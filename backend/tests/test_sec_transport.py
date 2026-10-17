"""SEC HTTP transport. Every request is served by httpx.MockTransport."""

from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from titan.common.settings import SecSettings, get_sec_settings
from titan.ingest.errors import (
    CacheIntegrityError,
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
from titan.ingest.raw_cache import RawResponseCache
from titan.ingest.sec_transport import (
    EndpointFreshness,
    SecHttpClient,
    canonical_sec_url,
    classify_endpoint,
)

_UA = "Titan Research titan@example.com"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK0000320193.json"
_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
_ARCHIVE = (
    "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-20240928.htm"
)
_SEARCH = "https://efts.sec.gov/LATEST/search-index?q=aapl"


class FakeClock:
    """Monotonic and wall clocks that advance only when the test says so."""

    def __init__(self) -> None:
        self.monotonic_time = 0.0
        self.wall = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.monotonic_time

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.monotonic_time += seconds

    def now(self) -> datetime:
        return self.wall

    def advance(self, seconds: float) -> None:
        self.wall += timedelta(seconds=seconds)


class Scripted:
    def __init__(self, planned: list[httpx.Response | Exception]) -> None:
        self.planned = planned
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if not self.planned:
            raise AssertionError(f"unexpected SEC request for {request.url}")
        item = self.planned.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _settings(tmp_path: Path, **overrides: object) -> SecSettings:
    values: dict[str, object] = {
        "sec_user_agent": _UA,
        "sec_cache_dir": tmp_path,
        "sec_timeout_seconds": 5.0,
        "sec_max_retries": 2,
        "sec_backoff_base_seconds": 0.5,
        "sec_backoff_max_seconds": 8.0,
        "sec_retry_after_max_seconds": 30.0,
        "sec_max_response_bytes": 1024,
        "sec_requests_per_second": 5.0,
        "sec_mutable_fresh_seconds": 60.0,
    }
    values.update(overrides)
    return SecSettings.model_validate(values)


def _client(
    settings: SecSettings,
    script: Scripted,
    clock: FakeClock | None = None,
) -> SecHttpClient:
    ticker = clock if clock is not None else FakeClock()
    return SecHttpClient(
        settings,
        transport=httpx.MockTransport(script),
        clock=ticker.now,
        monotonic=ticker.monotonic,
        sleep=ticker.sleep,
    )


def _ok(body: bytes = b"ok", content_type: str = "application/json") -> httpx.Response:
    return httpx.Response(200, content=body, headers={"content-type": content_type})


def test_request_sends_the_configured_user_agent(tmp_path: Path) -> None:
    script = Scripted([_ok()])
    with _client(_settings(tmp_path), script) as client:
        client.get(_SUBMISSIONS)
    assert script.requests[0].headers["user-agent"] == _UA
    assert "mozilla" not in script.requests[0].headers["user-agent"].lower()


def test_browser_user_agent_cannot_bypass_validation(tmp_path: Path) -> None:
    settings = SecSettings(sec_user_agent=_UA, sec_cache_dir=tmp_path)
    tampered = settings.model_copy(deep=True)
    object.__setattr__(tampered, "sec_user_agent", "Mozilla/5.0 person@example.com")
    script = Scripted([_ok()])
    with pytest.raises(SecConfigError, match="must not impersonate a browser"):
        _client(tampered, script)
    assert script.requests == []


def test_requests_are_spaced_at_five_per_second(tmp_path: Path) -> None:
    clock = FakeClock()
    seen: list[float] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(clock.monotonic())
        return _ok(request.url.host.encode("ascii"))

    with SecHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(record),
        clock=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ) as client:
        client.get(_SUBMISSIONS)
        client.get(_FACTS)
        client.get(_SEARCH)
    assert seen == pytest.approx([0.0, 0.2, 0.4])
    assert clock.sleeps == pytest.approx([0.2, 0.2])


def test_rate_above_five_per_second_cannot_bypass_validation(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"sec_requests_per_second": 50})
    script = Scripted([_ok()])
    with pytest.raises(SecConfigError, match="5 per second"):
        _client(settings, script)
    assert script.requests == []


def test_retries_do_not_bypass_the_rate_limit(tmp_path: Path) -> None:
    clock = FakeClock()
    seen: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(clock.monotonic())
        if len(seen) == 1:
            return httpx.Response(429)
        return _ok()

    with SecHttpClient(
        _settings(tmp_path, sec_backoff_base_seconds=0.01),
        transport=httpx.MockTransport(handler),
        clock=clock.now,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    ) as client:
        client.get(_SUBMISSIONS)
    assert seen == pytest.approx([0.0, 0.2])


def test_https_sec_hosts_are_fetched(tmp_path: Path) -> None:
    script = Scripted([_ok(b"submissions"), _ok(b"search")])
    with _client(_settings(tmp_path), script) as client:
        submissions = client.get(_SUBMISSIONS)
        search = client.get(_SEARCH)
    assert submissions.body == b"submissions"
    assert search.body == b"search"
    assert script.requests[0].url.host == "data.sec.gov"
    assert script.requests[1].url.host == "efts.sec.gov"


@pytest.mark.parametrize(
    "url",
    [
        "http://data.sec.gov/submissions/CIK0000320193.json",
        "https://www.sec.gov.evil.com/Archives/edgar/data/1/f.htm",
        "https://user:pass@data.sec.gov/submissions/CIK0000320193.json",
        "https://data.sec.gov:8443/submissions/CIK0000320193.json",
        "https://www.sec.gov/Archives/edgar/data/%2e%2e/secret",
        "https://www.se\u0441.gov/Archives/edgar/data/1/f.htm",
        "https://data.sec.gov./submissions/CIK0000320193.json",
        "ftp://data.sec.gov/submissions/CIK0000320193.json",
    ],
)
def test_url_outside_https_sec_domains_is_rejected(tmp_path: Path, url: str) -> None:
    script = Scripted([_ok()])
    with _client(_settings(tmp_path), script) as client, pytest.raises(SecUrlError):
        client.get(url)
    assert script.requests == []


def test_explicit_port_443_uses_the_same_cache_key(tmp_path: Path) -> None:
    script = Scripted([_ok(b"once")])
    with _client(_settings(tmp_path), script) as client:
        first = client.get("https://data.sec.gov:443/submissions/CIK0000320193.json")
        second = client.get(_SUBMISSIONS)
    assert first.body == second.body == b"once"
    assert second.from_cache is True
    assert len(script.requests) == 1
    assert canonical_sec_url("https://data.sec.gov:443/submissions/CIK0000320193.json") == (
        _SUBMISSIONS
    )


def test_redirects_are_not_followed(tmp_path: Path) -> None:
    script = Scripted(
        [
            httpx.Response(302, headers={"location": "https://example.com/stolen"}),
            _ok(b"should-not-run"),
        ]
    )
    with _client(_settings(tmp_path), script) as client:
        assert client.follow_redirects is False
        with pytest.raises(SecRedirectError) as caught:
            client.get(_SUBMISSIONS)
    assert caught.value.status_code == 302
    assert len(script.requests) == 1
    assert script.requests[0].url.host == "data.sec.gov"


def test_client_ignores_proxy_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    script = Scripted([_ok()])
    with _client(_settings(tmp_path), script) as client:
        assert client.trust_env is False
        client.get(_SUBMISSIONS)
    assert len(script.requests) == 1


def test_client_uses_the_configured_timeout(tmp_path: Path) -> None:
    script = Scripted([_ok()])
    with _client(_settings(tmp_path, sec_timeout_seconds=12.5), script) as client:
        assert client.timeout == httpx.Timeout(12.5)


def test_timeout_is_retried_until_success(tmp_path: Path) -> None:
    script = Scripted([httpx.ReadTimeout("timed out"), _ok(b"recovered")])
    with _client(_settings(tmp_path), script) as client:
        response = client.get(_SUBMISSIONS)
    assert response.body == b"recovered"
    assert len(script.requests) == 2


def test_timeout_stops_at_the_retry_bound(tmp_path: Path) -> None:
    script = Scripted([httpx.ReadTimeout("timed out") for _ in range(5)])
    client = _client(_settings(tmp_path, sec_max_retries=2), script)
    with client, pytest.raises(SecRetryError) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.attempts == 3
    assert len(script.requests) == 3
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)


def test_retry_count_above_the_cap_is_rejected(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(update={"sec_max_retries": 100})
    script = Scripted([httpx.Response(500)])
    with pytest.raises(SecConfigError, match="retries"):
        _client(settings, script)
    assert script.requests == []


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_retryable_status_then_success(tmp_path: Path, status_code: int) -> None:
    script = Scripted([httpx.Response(status_code), _ok(b"final")])
    with _client(_settings(tmp_path), script) as client:
        response = client.get(_SUBMISSIONS)
    assert response.body == b"final"
    loaded = client.cache.load(_SUBMISSIONS)
    assert loaded is not None
    assert loaded.body == b"final"


def test_connect_error_then_success(tmp_path: Path) -> None:
    script = Scripted([httpx.ConnectError("connection refused"), _ok(b"back")])
    with _client(_settings(tmp_path), script) as client:
        assert client.get(_SUBMISSIONS).body == b"back"
    assert len(script.requests) == 2


def test_exponential_backoff_is_capped(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([httpx.Response(500) for _ in range(4)])
    with _client(
        _settings(
            tmp_path,
            sec_max_retries=3,
            sec_backoff_base_seconds=0.5,
            sec_backoff_max_seconds=1.0,
        ),
        script,
        clock,
    ) as client, pytest.raises(SecRetryError) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.attempts == 4
    assert caught.value.status_code == 500
    assert clock.sleeps == pytest.approx([0.5, 1.0, 1.0])
    assert client.cache.load(_SUBMISSIONS) is None


def test_retries_exhausted_on_a_transient_error(tmp_path: Path) -> None:
    script = Scripted([httpx.ConnectError("down") for _ in range(3)])
    client = _client(_settings(tmp_path, sec_max_retries=2), script)
    with client, pytest.raises(SecRetryError) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.attempts == 3
    assert len(script.requests) == 3
    assert client.cache.load(_SUBMISSIONS) is None


@pytest.mark.parametrize("status_code", [400, 404, 408])
def test_client_error_is_not_retried(tmp_path: Path, status_code: int) -> None:
    script = Scripted([httpx.Response(status_code), _ok(b"later")])
    client = _client(_settings(tmp_path), script)
    with client, pytest.raises(SecHttpStatusError) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.status_code == status_code
    assert len(script.requests) == 1
    assert client.cache.load(_SUBMISSIONS) is None


def test_local_protocol_error_is_not_retried(tmp_path: Path) -> None:
    script = Scripted([httpx.LocalProtocolError("bad response line"), _ok()])
    client = _client(_settings(tmp_path), script)
    with client, pytest.raises(httpx.LocalProtocolError):
        client.get(_SUBMISSIONS)
    assert len(script.requests) == 1


def test_retry_after_seconds_are_honored(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([httpx.Response(429, headers={"retry-after": "2"}), _ok(b"later")])
    with _client(_settings(tmp_path), script, clock) as client:
        assert client.get(_SUBMISSIONS).body == b"later"
    assert clock.sleeps == pytest.approx([2.0])


def test_retry_after_http_date_is_honored(tmp_path: Path) -> None:
    clock = FakeClock()
    when = format_datetime(clock.wall + timedelta(seconds=3), usegmt=True)
    script = Scripted([httpx.Response(503, headers={"retry-after": when}), _ok()])
    with _client(_settings(tmp_path), script, clock) as client:
        client.get(_SUBMISSIONS)
    assert clock.sleeps == pytest.approx([3.0])


def test_invalid_retry_after_uses_exponential_backoff(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([httpx.Response(429, headers={"retry-after": "soon"}), _ok()])
    with _client(_settings(tmp_path), script, clock) as client:
        client.get(_SUBMISSIONS)
    assert clock.sleeps == pytest.approx([0.5])
    assert len(script.requests) == 2


def test_past_retry_after_does_not_sleep_a_negative_amount(tmp_path: Path) -> None:
    clock = FakeClock()
    when = format_datetime(clock.wall - timedelta(seconds=10), usegmt=True)
    script = Scripted([httpx.Response(429, headers={"retry-after": when}), _ok()])
    with _client(_settings(tmp_path), script, clock) as client:
        client.get(_SUBMISSIONS)
    assert clock.sleeps == pytest.approx([0.5])
    assert all(delay >= 0 for delay in clock.sleeps)


def test_retry_after_above_the_ceiling_is_not_shortened(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted(
        [httpx.Response(429, headers={"retry-after": "120"}), _ok(b"too-soon")]
    )
    client = _client(_settings(tmp_path, sec_retry_after_max_seconds=30), script, clock)
    with client, pytest.raises(SecRetryAfterExceeded) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.retry_after_seconds == 120
    assert caught.value.ceiling == 30
    assert len(script.requests) == 1
    assert clock.sleeps == []


def test_forbidden_response_is_not_retried(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted(
        [httpx.Response(403, headers={"retry-after": "1"}), _ok(b"should-not-run")]
    )
    with _client(_settings(tmp_path), script, clock) as client:
        with pytest.raises(SecForbiddenError) as caught:
            client.get(_SUBMISSIONS)
        assert client.cache.load(_SUBMISSIONS) is None
    assert caught.value.url == _SUBMISSIONS
    assert len(script.requests) == 1
    assert clock.sleeps == []


def test_body_at_the_size_limit_is_cached(tmp_path: Path) -> None:
    body = b"12345678"
    script = Scripted([_ok(body)])
    with _client(_settings(tmp_path, sec_max_response_bytes=8), script) as client:
        response = client.get(_SUBMISSIONS)
    assert response.body == body
    loaded = client.cache.load(_SUBMISSIONS)
    assert loaded is not None
    assert loaded.byte_count == 8


def test_body_over_the_size_limit_is_rejected_and_not_cached(tmp_path: Path) -> None:
    script = Scripted([_ok(b"123456789"), _ok(b"short")])
    with _client(_settings(tmp_path, sec_max_response_bytes=8), script) as client:
        with pytest.raises(SecResponseTooLarge) as caught:
            client.get(_SUBMISSIONS)
        assert client.cache.load(_SUBMISSIONS) is None
    assert caught.value.size == 9
    assert len(script.requests) == 1


def test_content_length_over_the_limit_is_rejected(tmp_path: Path) -> None:
    script = Scripted(
        [
            httpx.Response(200, content=b"x", headers={"content-length": "9"}),
            _ok(b"short"),
        ]
    )
    client = _client(_settings(tmp_path, sec_max_response_bytes=8), script)
    with client, pytest.raises(SecResponseTooLarge) as caught:
        client.get(_SUBMISSIONS)
    assert caught.value.size == 9
    assert len(script.requests) == 1


def test_invalid_content_length_is_not_retried(tmp_path: Path) -> None:
    script = Scripted(
        [httpx.Response(200, content=b"x", headers={"content-length": "nope"}), _ok()]
    )
    client = _client(_settings(tmp_path), script)
    with client, pytest.raises(SecTransportError, match="Content-Length"):
        client.get(_SUBMISSIONS)
    assert len(script.requests) == 1


def test_fresh_mutable_response_is_served_from_cache(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([_ok(b"v1"), _ok(b"v2")])
    with _client(_settings(tmp_path), script, clock) as client:
        first = client.get(_SUBMISSIONS)
        clock.advance(59)
        second = client.get(_SUBMISSIONS)
    assert first.body == second.body == b"v1"
    assert second.from_cache is True
    assert second.retrieved_at == first.retrieved_at == clock.wall - timedelta(seconds=59)
    assert second.freshness is EndpointFreshness.MUTABLE
    assert len(script.requests) == 1
    assert clock.sleeps == []


def test_stale_mutable_response_is_refetched(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([_ok(b"v1"), _ok(b"v2")])
    with _client(_settings(tmp_path, sec_mutable_fresh_seconds=60), script, clock) as client:
        first = client.get(_FACTS)
        clock.advance(60)
        second = client.get(_FACTS)
    assert first.from_cache is False
    assert second.from_cache is False
    assert second.body == b"v2"
    assert second.retrieved_at == clock.wall
    assert len(script.requests) == 2


def test_immutable_archive_is_reused_after_the_freshness_window(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([_ok(b"<html></html>", "text/html"), _ok(b"changed")])
    with _client(_settings(tmp_path, sec_mutable_fresh_seconds=60), script, clock) as client:
        first = client.get(_ARCHIVE)
        clock.advance(86_400)
        second = client.get(_ARCHIVE)
    assert classify_endpoint(_ARCHIVE) is EndpointFreshness.IMMUTABLE
    assert second.from_cache is True
    assert second.body == first.body == b"<html></html>"
    assert second.freshness is EndpointFreshness.IMMUTABLE
    assert len(script.requests) == 1


def test_archive_on_the_data_host_stays_mutable(tmp_path: Path) -> None:
    url = "https://data.sec.gov/Archives/edgar/data/320193/file.htm"
    assert classify_endpoint(url) is EndpointFreshness.MUTABLE
    clock = FakeClock()
    script = Scripted([_ok(b"v1"), _ok(b"v2")])
    with _client(_settings(tmp_path, sec_mutable_fresh_seconds=60), script, clock) as client:
        client.get(url)
        clock.advance(60)
        second = client.get(url)
    assert second.body == b"v2"


def test_zero_freshness_window_always_revalidates_mutable_urls(tmp_path: Path) -> None:
    script = Scripted([_ok(b"v1"), _ok(b"v2")])
    with _client(_settings(tmp_path, sec_mutable_fresh_seconds=0), script) as client:
        client.get(_SUBMISSIONS)
        second = client.get(_SUBMISSIONS)
    assert second.body == b"v2"
    assert len(script.requests) == 2


def test_stale_revalidation_failure_does_not_return_the_stale_body(tmp_path: Path) -> None:
    clock = FakeClock()
    script = Scripted([_ok(b"v1"), *[httpx.Response(500) for _ in range(3)]])
    with _client(_settings(tmp_path, sec_max_retries=2), script, clock) as client:
        first = client.get(_SUBMISSIONS)
        clock.advance(120)
        with pytest.raises(SecRetryError):
            client.get(_SUBMISSIONS)
        loaded = client.cache.load(_SUBMISSIONS)
    assert loaded is not None
    assert loaded.body == first.body == b"v1"


def test_tampered_cache_is_not_served(tmp_path: Path) -> None:
    script = Scripted([_ok(b"intact"), _ok(b"replacement")])
    with _client(_settings(tmp_path), script) as client:
        client.get(_SUBMISSIONS)
        path = client.cache.entry_path(_SUBMISSIONS)
        blob = bytearray(path.read_bytes())
        blob[-1] ^= 0x01
        path.write_bytes(bytes(blob))
        with pytest.raises(CacheIntegrityError, match="SHA-256"):
            client.get(_SUBMISSIONS)
    assert len(script.requests) == 1


def test_naive_clock_does_not_send_a_request(tmp_path: Path) -> None:
    script = Scripted([_ok()])

    def naive() -> datetime:
        return datetime(2026, 1, 15, 12, 0)

    with SecHttpClient(
        _settings(tmp_path),
        transport=httpx.MockTransport(script),
        clock=naive,
        monotonic=FakeClock().monotonic,
        sleep=FakeClock().sleep,
    ) as client, pytest.raises(SecConfigError, match="timezone-aware"):
        client.get(_SUBMISSIONS)
    assert script.requests == []


def test_user_agent_comes_from_sec_settings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", _UA)
    get_sec_settings.cache_clear()
    script = Scripted([_ok(b"from-env")])
    try:
        clock = FakeClock()
        with SecHttpClient(
            transport=httpx.MockTransport(script),
            cache=RawResponseCache(tmp_path, max_bytes=1024),
            clock=clock.now,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
        ) as client:
            response = client.get(_SUBMISSIONS)
    finally:
        get_sec_settings.cache_clear()
    assert response.body == b"from-env"
    assert script.requests[0].headers["user-agent"] == _UA


def test_non_https_archive_url_is_not_classified_as_immutable() -> None:
    with pytest.raises(SecUrlError):
        classify_endpoint(
            "http://www.sec.gov/Archives/edgar/data/320193/000032019324000123/a.htm"
        )
