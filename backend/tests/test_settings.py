"""Database URL normalization and SEC fair-access settings."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from titan.common.settings import (
    MAX_SEC_REQUESTS_PER_SECOND,
    DatabaseSettings,
    SecSettings,
    get_sec_settings,
)

_UA = "Titan Research titan@example.com"
_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_plain_postgres_url_uses_psycopg() -> None:
    settings = DatabaseSettings(
        database_url="postgresql://titan:titan@localhost:5432/titan",
    )
    assert settings.sqlalchemy_url == "postgresql+psycopg://titan:titan@localhost:5432/titan"


def test_psycopg_url_is_unchanged() -> None:
    url = "postgresql+psycopg://titan:titan@localhost:5432/titan"
    settings = DatabaseSettings(database_url=url)
    assert settings.sqlalchemy_url == url


def test_non_postgres_url_is_rejected() -> None:
    with pytest.raises(ValidationError):
        DatabaseSettings(database_url="sqlite:///titan.db")


def test_descriptive_sec_user_agent_is_accepted() -> None:
    settings = SecSettings(sec_user_agent="  Ada Lovelace   ada@example.com  ")
    assert settings.sec_user_agent == "Ada Lovelace ada@example.com"


def test_sec_user_agent_is_required(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("SEC_USER_AGENT", raising=False)
    empty = tmp_path / "empty.env"
    empty.write_text("")
    with pytest.raises(ValidationError, match="sec_user_agent"):
        SecSettings(_env_file=empty)


def test_sec_user_agent_without_contact_email_is_rejected() -> None:
    with pytest.raises(ValidationError, match="contact email"):
        SecSettings(sec_user_agent="Titan Research")


def test_browser_user_agent_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must not impersonate a browser"):
        SecSettings(sec_user_agent="Mozilla/5.0 Titan Research titan@example.com")


def test_sec_user_agent_with_a_newline_is_rejected() -> None:
    with pytest.raises(ValidationError, match="control character"):
        SecSettings(sec_user_agent="Titan Research titan@example.com\r\nX-Ignore: 1")


def test_sec_request_rate_defaults_to_the_fair_access_cap() -> None:
    settings = SecSettings(sec_user_agent=_UA)
    assert settings.sec_requests_per_second == MAX_SEC_REQUESTS_PER_SECOND


def test_sec_request_rate_above_five_per_second_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SecSettings(sec_user_agent=_UA, sec_requests_per_second=5.1)


def test_non_positive_sec_request_rate_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SecSettings(sec_user_agent=_UA, sec_requests_per_second=0)


def test_positive_sec_timeout_is_accepted() -> None:
    settings = SecSettings(sec_user_agent=_UA, sec_timeout_seconds=12.5)
    assert settings.sec_timeout_seconds == 12.5


def test_non_positive_sec_timeout_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SecSettings(sec_user_agent=_UA, sec_timeout_seconds=0)


def test_relative_sec_cache_dir_is_resolved_from_the_repo_root() -> None:
    settings = SecSettings(sec_user_agent=_UA, sec_cache_dir=Path("data/raw"))
    assert settings.sec_cache_dir == _REPO_ROOT / "data" / "raw"


def test_empty_sec_cache_dir_is_rejected() -> None:
    with pytest.raises(ValidationError, match="sec_cache_dir"):
        SecSettings(sec_user_agent=_UA, sec_cache_dir="")


def test_backoff_ceiling_below_the_base_delay_is_rejected() -> None:
    with pytest.raises(ValidationError, match="sec_backoff_max_seconds"):
        SecSettings(
            sec_user_agent=_UA,
            sec_backoff_base_seconds=10,
            sec_backoff_max_seconds=1,
        )


def test_negative_mutable_freshness_window_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SecSettings(sec_user_agent=_UA, sec_mutable_fresh_seconds=-1)


def test_get_sec_settings_reads_sec_user_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SEC_USER_AGENT", _UA)
    get_sec_settings.cache_clear()
    try:
        assert get_sec_settings().sec_user_agent == _UA
    finally:
        get_sec_settings.cache_clear()
