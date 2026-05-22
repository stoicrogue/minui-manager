"""SQLAlchemy setup. SQLite at ./data/app.db.

Schema is created on startup with ``Base.metadata.create_all``. Small
forward-compatible column additions are handled inline via
:func:`_apply_lightweight_migrations`. Heavier changes should switch to
Alembic.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.paths import DB_PATH, ensure_data_dirs


class Base(DeclarativeBase):
    pass


def _make_engine():
    ensure_data_dirs()
    return create_engine(
        f"sqlite:///{DB_PATH}",
        # check_same_thread=False so FastAPI can use a session across the
        # request lifecycle without complaints; we still scope a session
        # per request.
        connect_args={"check_same_thread": False},
        future=True,
    )


_engine = _make_engine()
SessionLocal = sessionmaker(bind=_engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    """Create all tables. Idempotent."""
    # Import models so their tables are registered with Base.metadata.
    from app import models  # noqa: F401

    Base.metadata.create_all(_engine)
    _apply_lightweight_migrations()


def _apply_lightweight_migrations() -> None:
    """Add columns that newer code expects but older DBs don't have.

    SQLite's ``ALTER TABLE ... ADD COLUMN`` is fully online and only runs
    once per upgrade since we check the column list first.
    """
    inspector = inspect(_engine)
    with _engine.begin() as conn:
        # disc_filenames was added when multi-disk support landed. Older
        # DBs (single-disk only) are missing it.
        for table in ("library_games", "archived_games"):
            existing = inspector.has_table(table)
            if not existing:
                continue
            cols = {c["name"] for c in inspector.get_columns(table)}
            if "disc_filenames" not in cols:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN disc_filenames TEXT"))


@contextmanager
def session_scope() -> Iterator[Session]:
    """Yield a session; commit on success, rollback on exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def reset_engine_for_tests() -> None:
    """Re-create the engine after the data dir has been redirected (tests).

    The paths module is reloaded by the ``tmp_project_root`` fixture, which
    moves DB_PATH; the engine bound at import time still points at the old
    location. Call this from tests after the fixture has run.
    """
    global _engine, SessionLocal
    _engine.dispose()
    _engine = _make_engine()
    SessionLocal.configure(bind=_engine)
