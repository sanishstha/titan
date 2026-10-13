"""Database settings loaded from the environment."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[4]
_DEFAULT_URL = "postgresql+psycopg://titan:titan@localhost:5432/titan"


class DatabaseSettings(BaseSettings):
    """Synchronous Postgres URL for the psycopg v3 driver.

    ``DATABASE_URL`` may be ``postgresql://`` or ``postgresql+psycopg://``.
    A URL without a driver is rewritten to ``postgresql+psycopg``.
    When the variable is unset, the local Compose database is used.
    """

    model_config = SettingsConfigDict(
        env_file=(_REPO_ROOT / ".env",),
        env_file_encoding="utf-8",
        extra="ignore",
    )

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
