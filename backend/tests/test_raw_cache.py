"""Raw SEC response cache: atomic writes, SHA-256, and retrieval timestamps."""

import hashlib
import json
import os
import struct
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from titan.ingest.errors import CacheIntegrityError, SecResponseTooLarge
from titan.ingest.raw_cache import CACHE_MAGIC, RawResponseCache

_URL = "https://data.sec.gov/submissions/CIK0000320193.json"
_WHEN = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)


def _cache(tmp_path: Path, *, max_bytes: int = 1024) -> RawResponseCache:
    return RawResponseCache(tmp_path, max_bytes=max_bytes)


def _frame(header: dict[str, object], body: bytes) -> bytes:
    raw = json.dumps(header).encode("utf-8")
    return CACHE_MAGIC + struct.pack(">I", len(raw)) + raw + body


def test_store_then_load_round_trips_the_body_and_leaves_no_temp_file(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    body = b'{"cik":"0000320193"}'
    stored = cache.store(
        _URL,
        body,
        retrieved_at=_WHEN,
        status_code=200,
        content_type="application/json",
    )
    loaded = cache.load(_URL)
    assert loaded == stored
    assert loaded is not None
    assert loaded.body == body
    assert loaded.sha256 == hashlib.sha256(body).hexdigest()
    assert list(tmp_path.rglob("*.tmp")) == []
    assert cache.entry_path(_URL).resolve().is_relative_to(tmp_path.resolve())


def test_failed_replace_keeps_the_previous_entry_and_removes_the_temp_file(
    tmp_path: Path,
) -> None:
    cache = _cache(tmp_path)
    cache.store(_URL, b"v1", retrieved_at=_WHEN, status_code=200, content_type=None)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk full")

    failing = RawResponseCache(tmp_path, max_bytes=1024, replacer=fail_replace)
    with pytest.raises(OSError, match="disk full"):
        failing.store(
            _URL,
            b"v2",
            retrieved_at=_WHEN + timedelta(hours=1),
            status_code=200,
            content_type=None,
        )
    loaded = cache.load(_URL)
    assert loaded is not None
    assert loaded.body == b"v1"
    assert list(tmp_path.rglob("*.tmp")) == []


def test_failed_first_write_does_not_publish_a_partial_entry(tmp_path: Path) -> None:
    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("disk full")

    cache = RawResponseCache(tmp_path, max_bytes=1024, replacer=fail_replace)
    with pytest.raises(OSError, match="disk full"):
        cache.store(_URL, b"v1", retrieved_at=_WHEN, status_code=200, content_type=None)
    assert cache.load(_URL) is None
    assert list(tmp_path.rglob("*.tmp")) == []


def test_cached_body_matches_its_sha256(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    body = b"filing-bytes"
    entry = cache.store(_URL, body, retrieved_at=_WHEN, status_code=200, content_type="text/html")
    assert entry.sha256 == hashlib.sha256(body).hexdigest()
    loaded = cache.load(_URL)
    assert loaded is not None
    assert loaded.sha256 == entry.sha256


def test_tampered_body_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    cache.store(_URL, b"intact", retrieved_at=_WHEN, status_code=200, content_type=None)
    path = cache.entry_path(_URL)
    blob = bytearray(path.read_bytes())
    blob[-1] ^= 0x01
    path.write_bytes(bytes(blob))
    with pytest.raises(CacheIntegrityError, match="SHA-256"):
        cache.load(_URL)


def test_retrieval_timestamp_is_recorded(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    entry = cache.store(_URL, b"{}", retrieved_at=_WHEN, status_code=200, content_type=None)
    assert entry.retrieved_at == _WHEN
    loaded = cache.load(_URL)
    assert loaded is not None
    assert loaded.retrieved_at == _WHEN


def test_missing_retrieval_timestamp_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    body = b"{}"
    header: dict[str, object] = {
        "url": _URL,
        "sha256": hashlib.sha256(body).hexdigest(),
        "status_code": 200,
        "content_type": None,
        "byte_count": len(body),
    }
    path = cache.entry_path(_URL)
    path.parent.mkdir(parents=True)
    path.write_bytes(_frame(header, body))
    with pytest.raises(CacheIntegrityError, match="retrieved_at"):
        cache.load(_URL)


def test_naive_retrieval_timestamp_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(ValueError, match="timezone-aware"):
        cache.store(
            _URL,
            b"{}",
            retrieved_at=datetime(2026, 1, 15, 12, 0),
            status_code=200,
            content_type=None,
        )
    assert cache.load(_URL) is None


def test_naive_timestamp_on_disk_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    body = b"{}"
    header: dict[str, object] = {
        "url": _URL,
        "retrieved_at": "2026-01-15T12:00:00",
        "sha256": hashlib.sha256(body).hexdigest(),
        "status_code": 200,
        "content_type": None,
        "byte_count": len(body),
    }
    path = cache.entry_path(_URL)
    path.parent.mkdir(parents=True)
    path.write_bytes(_frame(header, body))
    with pytest.raises(CacheIntegrityError, match="timezone-aware"):
        cache.load(_URL)


def test_url_key_cannot_escape_the_cache_directory(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    escaped = cache.entry_path("../../etc/passwd")
    assert escaped.resolve().is_relative_to(tmp_path.resolve())
    assert ".." not in escaped.parts
    assert os.path.commonpath((tmp_path.resolve(), escaped.resolve())) == str(tmp_path.resolve())


def test_swapped_cache_file_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    other = "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json"
    cache.store(_URL, b"submissions", retrieved_at=_WHEN, status_code=200, content_type=None)
    stolen = cache.entry_path(_URL).read_bytes()
    destination = cache.entry_path(other)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(stolen)
    with pytest.raises(CacheIntegrityError, match="does not match the request"):
        cache.load(other)


def test_error_status_is_not_stored(tmp_path: Path) -> None:
    cache = _cache(tmp_path)
    with pytest.raises(ValueError, match="HTTP 200"):
        cache.store(_URL, b"nope", retrieved_at=_WHEN, status_code=500, content_type=None)
    assert cache.load(_URL) is None


def test_store_rejects_a_body_over_the_size_limit(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=4)
    with pytest.raises(SecResponseTooLarge):
        cache.store(_URL, b"12345", retrieved_at=_WHEN, status_code=200, content_type=None)
    assert cache.load(_URL) is None


def test_oversized_cache_file_is_rejected(tmp_path: Path) -> None:
    cache = _cache(tmp_path, max_bytes=8)
    path = cache.entry_path(_URL)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 100_000)
    with pytest.raises(CacheIntegrityError, match="size limit"):
        cache.load(_URL)


def test_cache_directory_that_is_a_file_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "not-a-directory"
    root.write_text("occupied")
    cache = RawResponseCache(root, max_bytes=100)
    with pytest.raises(OSError):
        cache.store(_URL, b"{}", retrieved_at=_WHEN, status_code=200, content_type=None)
