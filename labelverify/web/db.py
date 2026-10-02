"""Database setup: SQLAlchemy engine, session factory, and the FastAPI dependency."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from fastapi import Request
from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///./data/labelverify.db"


class Base(DeclarativeBase):
    pass


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        path = url.replace("sqlite:///", "", 1)
        if path and path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA busy_timeout=30000")  # ms; writers are short, see services
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db(engine: Engine) -> None:
    from . import models  # noqa: F401  (registers tables)

    Base.metadata.create_all(engine)
    _add_missing_columns(engine)


# Columns added after the first release. create_all() never alters existing tables, so a
# database created by an older version gets them here. Values are SQL literals.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "label_images": {
        "version": "INTEGER NOT NULL DEFAULT 1",
        "panel": "INTEGER NOT NULL DEFAULT 1",
    },
    "verification_runs": {"image_version": "INTEGER NOT NULL DEFAULT 1"},
    "applications": {
        "beverage_type_inferred": "BOOLEAN NOT NULL DEFAULT 0",
        "extraction_json": "TEXT",
        "extraction_version": "INTEGER NOT NULL DEFAULT 0",
        "extraction_ms": "INTEGER NOT NULL DEFAULT 0",
        "extraction_extractor": "VARCHAR(80) NOT NULL DEFAULT ''",
        # Label statements filed as printed (added with the editable step-2 statements).
        "qualifying_phrase": "TEXT",
        "health_warning": "TEXT",
        "sulfite_declaration": "TEXT",
        "appellation": "TEXT",
        "vintage_year": "TEXT",
        "estate_bottled": "TEXT",
        "age_statement": "TEXT",
        "bottled_in_bond": "TEXT",
        "blend_percentage": "TEXT",
        "strength_claim": "TEXT",
    },
}


def _add_missing_columns(engine: Engine) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(engine)
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            if table not in inspector.get_table_names():
                continue
            existing = {c["name"] for c in inspector.get_columns(table)}
            for name, ddl in columns.items():
                if name not in existing:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def get_db(request: Request) -> Iterator[Session]:
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
