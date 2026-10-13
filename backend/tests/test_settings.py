"""Database URL normalization."""

import pytest
from pydantic import ValidationError

from titan.common.settings import DatabaseSettings


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
