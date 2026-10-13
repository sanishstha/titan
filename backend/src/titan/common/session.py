"""Synchronous SQLAlchemy sessions using the psycopg driver."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from titan.common.settings import DatabaseSettings, get_database_settings


def create_db_engine(settings: DatabaseSettings | None = None) -> Engine:
    """Create a synchronous engine. The driver is selected by the URL."""
    resolved = settings if settings is not None else get_database_settings()
    return create_engine(resolved.sqlalchemy_url, pool_pre_ping=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Return a session factory bound to ``engine``."""
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine."""
    return create_db_engine()


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    """Return the process-wide session factory."""
    return create_session_factory(get_engine())


@contextmanager
def open_session(factory: sessionmaker[Session] | None = None) -> Iterator[Session]:
    """Yield a session and close it. The caller commits or rolls back."""
    maker = factory if factory is not None else get_session_factory()
    session = maker()
    try:
        yield session
    finally:
        session.close()
