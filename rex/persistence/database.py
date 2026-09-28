"""REX Database Layer (REX-004).

Provides SQLAlchemy 2.x engine management, session factory, transaction boundaries,
and SQLite foreign-key enforcement.
"""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from rex.config import get_settings


class Base(DeclarativeBase):
    """Base declarative class for all REX database models."""


def _enable_sqlite_foreign_keys(dbapi_connection: Any, connection_record: Any) -> None:
    """Ensure SQLite enforces foreign key constraints on every connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def create_db_engine(database_url: str | None = None, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.x Engine with foreign key enforcement and local directory initialization."""
    url = database_url or get_settings().persistence.database_url

    # Ensure parent directory exists for file-based SQLite databases
    if url.startswith("sqlite:///") and ":memory:" not in url:
        # Handle Windows and POSIX path extraction from file URLs
        db_path_str = url.replace("sqlite:///", "")
        if "?" in db_path_str:
            db_path_str = db_path_str.split("?")[0]
        db_path = Path(db_path_str)
        db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine(url, echo=echo, future=True)

    # Attach SQLite PRAGMA listener if SQLite dialect
    if engine.dialect.name == "sqlite":
        event.listen(engine, "connect", _enable_sqlite_foreign_keys)

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a thread-safe Session factory bound to the given engine."""
    return sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def init_db(engine: Engine) -> None:
    """Create all schema tables in the database if they do not already exist."""
    Base.metadata.create_all(bind=engine)


@contextmanager
def get_db_session(
    session_factory: sessionmaker[Session],
) -> Generator[Session, None, None]:
    """Transactional context manager for database sessions.

    Commits on successful completion, rolls back on exception, and guarantees closure.
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
