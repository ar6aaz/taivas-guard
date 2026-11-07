"""Operational database: engine, sessions and schema creation."""
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC, so SQLite and Postgres store and compare the same values."""
    return datetime.now(UTC).replace(tzinfo=None)


@lru_cache
def get_engine() -> Engine:
    url = make_url(get_settings().database_url)
    if url.get_backend_name() != "sqlite":
        return create_engine(url, pool_pre_ping=True)
    if url.database:
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={"timeout": 30})

    @event.listens_for(engine, "connect")
    def _pragmas(conn, _record):  # several service processes share the file
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")

    return engine


@lru_cache
def _factory() -> sessionmaker[Session]:
    return sessionmaker(get_engine(), expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    session = _factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    from . import models  # noqa: F401  (registers the tables)

    Base.metadata.create_all(get_engine())


def reset_caches() -> None:
    """For tests: forget the engine after settings change."""
    _factory.cache_clear()
    get_engine.cache_clear()
