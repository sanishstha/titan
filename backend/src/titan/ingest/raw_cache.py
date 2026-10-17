"""Atomic on-disk cache of raw SEC responses.

Each entry is one file. The file is written to a temporary name in the same
directory, fsynced, then published with ``os.replace``. Readers therefore see
either the previous complete entry or the new one.

The on-disk layout is ``CACHE_MAGIC``, a big-endian header length, a JSON
header, then the body. The header records the URL, the SHA-256 of the body,
and the UTC time the bytes were retrieved. The path is a hash of the URL, so a
URL cannot escape the cache directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import cast
from uuid import uuid4

from titan.ingest.errors import CacheIntegrityError, SecResponseTooLarge

CACHE_MAGIC = b"TITANSEC1"
_HEADER_LENGTH = 4
_MAX_HEADER_BYTES = 65_536


@dataclass(frozen=True)
class RawCacheEntry:
    """One verified raw response read from disk."""

    url: str
    body: bytes
    sha256: str
    retrieved_at: datetime
    status_code: int
    content_type: str | None
    byte_count: int


def _replace_file(source: Path, destination: Path) -> None:
    os.replace(source, destination)


class RawResponseCache:
    """Content-addressed store of raw SEC response bodies."""

    def __init__(
        self,
        root: Path,
        *,
        max_bytes: int,
        replacer: Callable[[Path, Path], None] | None = None,
    ) -> None:
        if max_bytes <= 0:
            raise ValueError("max_bytes must be positive")
        self._root = root
        self._max_bytes = max_bytes
        self._replace = _replace_file if replacer is None else replacer

    @property
    def root(self) -> Path:
        return self._root

    @property
    def max_bytes(self) -> int:
        return self._max_bytes

    def entry_path(self, url: str) -> Path:
        """Return the only path where ``url`` may be stored."""
        if url == "":
            raise ValueError("url must be non-empty")
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        path = self._root / digest[:2] / digest
        resolved_root = self._root.resolve()
        resolved = path.resolve()
        if not resolved.is_relative_to(resolved_root):
            raise CacheIntegrityError("cache path escaped the cache directory", url=url)
        return path

    def load(self, url: str) -> RawCacheEntry | None:
        """Return the entry for ``url``, or ``None`` when it is absent.

        A present file that fails the SHA-256 or timestamp check raises
        ``CacheIntegrityError`` instead of being treated as a miss.
        """
        path = self.entry_path(url)
        if not path.is_file():
            return None
        overhead = len(CACHE_MAGIC) + _HEADER_LENGTH + _MAX_HEADER_BYTES
        if path.stat().st_size > self._max_bytes + overhead:
            raise CacheIntegrityError("cache entry exceeds the response size limit", url=url)
        return _decode(path.read_bytes(), expected_url=url)

    def store(
        self,
        url: str,
        body: bytes,
        *,
        retrieved_at: datetime,
        status_code: int,
        content_type: str | None,
    ) -> RawCacheEntry:
        """Atomically store a 200 response and return the verified entry."""
        if url == "":
            raise ValueError("url must be non-empty")
        if type(status_code) is not int or status_code != 200:
            raise ValueError("only HTTP 200 responses are cached")
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("retrieved_at must be timezone-aware")
        if len(body) > self._max_bytes:
            raise SecResponseTooLarge(url, len(body), self._max_bytes)
        entry = RawCacheEntry(
            url=url,
            body=body,
            sha256=hashlib.sha256(body).hexdigest(),
            retrieved_at=retrieved_at,
            status_code=200,
            content_type=content_type,
            byte_count=len(body),
        )
        self._atomic_write(self.entry_path(url), _encode(entry))
        return entry

    def _atomic_write(self, destination: Path, payload: bytes) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.parent / f".{destination.name}.{os.getpid()}.{uuid4().hex}.tmp"
        try:
            with temporary.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            self._replace(temporary, destination)
        except BaseException:
            if temporary.exists():
                temporary.unlink()
            raise


def _encode(entry: RawCacheEntry) -> bytes:
    header = {
        "url": entry.url,
        "retrieved_at": entry.retrieved_at.isoformat(),
        "sha256": entry.sha256,
        "status_code": entry.status_code,
        "content_type": entry.content_type,
        "byte_count": entry.byte_count,
    }
    encoded = json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    if len(encoded) > _MAX_HEADER_BYTES:
        raise CacheIntegrityError("cache header is too large", url=entry.url)
    return CACHE_MAGIC + struct.pack(">I", len(encoded)) + encoded + entry.body


def _decode(payload: bytes, *, expected_url: str) -> RawCacheEntry:
    prefix = len(CACHE_MAGIC) + _HEADER_LENGTH
    if len(payload) < prefix or not payload.startswith(CACHE_MAGIC):
        raise CacheIntegrityError("unrecognized or truncated cache entry", url=expected_url)
    header_len = struct.unpack(">I", payload[len(CACHE_MAGIC) : prefix])[0]
    if header_len > _MAX_HEADER_BYTES:
        raise CacheIntegrityError("cache header is too large", url=expected_url)
    header_end = prefix + header_len
    if header_end > len(payload):
        raise CacheIntegrityError("truncated cache header", url=expected_url)
    header = _header_object(payload[prefix:header_end], expected_url)
    body = payload[header_end:]
    url = _required_str(header, "url", expected_url)
    if url != expected_url:
        raise CacheIntegrityError("cache entry URL does not match the request", url=expected_url)
    claimed = _required_str(header, "sha256", expected_url)
    if len(claimed) != 64 or any(character not in "0123456789abcdef" for character in claimed):
        raise CacheIntegrityError("cache entry has an invalid SHA-256", url=expected_url)
    digest = hashlib.sha256(body).hexdigest()
    if digest != claimed:
        raise CacheIntegrityError("cache body does not match its SHA-256", url=expected_url)
    byte_count = _required_int(header, "byte_count", expected_url)
    if byte_count != len(body):
        raise CacheIntegrityError(
            "cache entry byte count does not match the body",
            url=expected_url,
        )
    status_code = _required_int(header, "status_code", expected_url)
    if status_code != 200:
        raise CacheIntegrityError("cache entry is not an HTTP 200 response", url=expected_url)
    content_type = _content_type(header.get("content_type"), expected_url)
    retrieved_at = _retrieved_at(header.get("retrieved_at"), expected_url)
    return RawCacheEntry(
        url=url,
        body=body,
        sha256=digest,
        retrieved_at=retrieved_at,
        status_code=status_code,
        content_type=content_type,
        byte_count=byte_count,
    )


def _header_object(raw: bytes, url: str) -> dict[str, object]:
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise CacheIntegrityError("cache header is not JSON", url=url) from exc
    if not isinstance(loaded, dict):
        raise CacheIntegrityError("cache header must be a JSON object", url=url)
    header: dict[str, object] = {}
    for key, value in cast(dict[object, object], loaded).items():
        if not isinstance(key, str):
            raise CacheIntegrityError("cache header keys must be strings", url=url)
        header[key] = value
    return header


def _required_int(header: dict[str, object], key: str, url: str) -> int:
    value = header.get(key)
    if type(value) is not int:
        raise CacheIntegrityError(f"cache header has an invalid {key}", url=url)
    return value


def _content_type(value: object, url: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    raise CacheIntegrityError("cache entry content type is invalid", url=url)


def _required_str(header: dict[str, object], key: str, url: str) -> str:
    value = header.get(key)
    if not isinstance(value, str) or value == "":
        raise CacheIntegrityError(f"cache header is missing {key}", url=url)
    return value


def _retrieved_at(value: object, url: str) -> datetime:
    if not isinstance(value, str) or value == "":
        raise CacheIntegrityError("cache header is missing retrieved_at", url=url)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CacheIntegrityError("cache header retrieved_at is invalid", url=url) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CacheIntegrityError("cache header retrieved_at must be timezone-aware", url=url)
    return parsed
