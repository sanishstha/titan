"""Process settings loaded from the environment.

Database settings and SEC fair-access settings share one ``.env`` file and the
same ``BaseSettings`` rules. Each class reads only its own fields.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_URL = "postgresql+psycopg://titan:titan@localhost:5432/titan"
_ENV_FILE = (_REPO_ROOT / ".env",)
_ENV_CONFIG = SettingsConfigDict(
    env_file=_ENV_FILE,
    env_file_encoding="utf-8",
    extra="ignore",
)

# Hard caps. Raising them would weaken SEC fair-access rules, so both the
# settings validators and the HTTP client reject anything outside these bounds.
MAX_SEC_REQUESTS_PER_SECOND = 5.0
MAX_SEC_RETRIES = 5
MAX_SEC_TIMEOUT_SECONDS = 120.0
MAX_SEC_RESPONSE_BYTES = 128 * 1024 * 1024
MAX_SEC_BACKOFF_SECONDS = 120.0
MAX_SEC_RETRY_AFTER_SECONDS = 300.0
MAX_SEC_MUTABLE_FRESH_SECONDS = 7 * 24 * 3600.0
MAX_SEC_USER_AGENT_LENGTH = 256

_CONTACT_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_BROWSER_MARKERS = (
    "mozilla/",
    "applewebkit",
    "chrome/",
    "safari/",
    "gecko/",
    "edg/",
)


class DatabaseSettings(BaseSettings):
    """Synchronous Postgres URL for the psycopg v3 driver.

    ``DATABASE_URL`` may be ``postgresql://`` or ``postgresql+psycopg://``.
    A URL without a driver is rewritten to ``postgresql+psycopg``.
    When the variable is unset, the local Compose database is used.
    """

    model_config = _ENV_CONFIG

    database_url: str = Field(default=_DEFAULT_URL)

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        if value.startswith("postgres://"):
            value = "postgresql://" + value.removeprefix("postgres://")
        if value.startswith("postgresql://"):
            value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("database_url must use the postgresql+psycopg driver")
        return value

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url


@lru_cache(maxsize=1)
def get_database_settings() -> DatabaseSettings:
    """Return process-wide database settings."""
    return DatabaseSettings()


def validate_sec_user_agent(value: str) -> str:
    """Return a descriptive SEC User-Agent, or raise ``ValueError``.

    SEC fair access requires the operator's name and a contact email. A browser
    or library default is rejected, as is a value that could inject header lines.
    """
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("SEC_USER_AGENT contains a control character")
    stripped = " ".join(value.split())
    if stripped == "" or len(stripped) > MAX_SEC_USER_AGENT_LENGTH:
        raise ValueError("SEC_USER_AGENT must identify the operator and include a contact email")
    lowered = stripped.lower()
    if any(marker in lowered for marker in _BROWSER_MARKERS):
        raise ValueError("SEC_USER_AGENT must not impersonate a browser")
    emails = [token.strip("<>") for token in stripped.split() if "@" in token]
    names = [token for token in stripped.split() if "@" not in token]
    if not emails or any(_CONTACT_EMAIL.fullmatch(email) is None for email in emails):
        raise ValueError("SEC_USER_AGENT must include a contact email")
    if not any(any(character.isalpha() for character in token) for token in names):
        raise ValueError("SEC_USER_AGENT must include the operator's name and a contact email")
    return stripped


class SecSettings(BaseSettings):
    """SEC EDGAR client settings.

    ``SEC_USER_AGENT`` is required. The request rate cannot be set above 5 per
    second. Mutable companyfacts and submissions responses stay fresh only for
    ``sec_mutable_fresh_seconds``; filing archives are immutable in the client.
    """

    model_config = _ENV_CONFIG

    sec_user_agent: str
    sec_timeout_seconds: float = Field(default=30.0, gt=0, le=MAX_SEC_TIMEOUT_SECONDS)
    sec_max_retries: int = Field(default=3, ge=0, le=MAX_SEC_RETRIES)
    sec_backoff_base_seconds: float = Field(default=0.5, gt=0, le=MAX_SEC_BACKOFF_SECONDS)
    sec_backoff_max_seconds: float = Field(default=30.0, gt=0, le=MAX_SEC_BACKOFF_SECONDS)
    sec_retry_after_max_seconds: float = Field(
        default=60.0,
        gt=0,
        le=MAX_SEC_RETRY_AFTER_SECONDS,
    )
    sec_max_response_bytes: int = Field(default=64 * 1024 * 1024, gt=0, le=MAX_SEC_RESPONSE_BYTES)
    sec_requests_per_second: float = Field(default=5.0, gt=0, le=MAX_SEC_REQUESTS_PER_SECOND)
    sec_cache_dir: Path = Field(default=_REPO_ROOT / "data" / "raw")
    sec_mutable_fresh_seconds: float = Field(
        default=3600.0,
        ge=0,
        le=MAX_SEC_MUTABLE_FRESH_SECONDS,
    )

    @field_validator("sec_user_agent")
    @classmethod
    def _descriptive_user_agent(cls, value: str) -> str:
        return validate_sec_user_agent(value)

    @field_validator("sec_cache_dir", mode="before")
    @classmethod
    def _resolve_cache_dir(cls, value: object) -> Path:
        if isinstance(value, str) and value.strip() == "":
            raise ValueError("sec_cache_dir must not be empty")
        if not isinstance(value, str | Path):
            raise TypeError("sec_cache_dir must be a path")
        path = Path(value)
        if path == Path("."):
            raise ValueError("sec_cache_dir must not be empty")
        if not path.is_absolute():
            path = _REPO_ROOT / path
        if path == _REPO_ROOT:
            raise ValueError("sec_cache_dir must be a dedicated directory")
        return path

    @model_validator(mode="after")
    def _backoff_is_bounded(self) -> Self:
        if self.sec_backoff_max_seconds < self.sec_backoff_base_seconds:
            raise ValueError(
                "sec_backoff_max_seconds must be greater than or equal to "
                "sec_backoff_base_seconds"
            )
        return self


@lru_cache(maxsize=1)
def get_sec_settings() -> SecSettings:
    """Return process-wide SEC settings."""
    # SEC_USER_AGENT is required and is loaded from the environment.
    return SecSettings()  # type: ignore[call-arg]
