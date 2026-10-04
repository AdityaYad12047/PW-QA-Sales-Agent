"""
backend/app/db/engine.py
SQLAlchemy engine + session factory.
SQLite for dev; swap DATABASE_URL to postgres in production (see README).
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine():
    settings = get_settings()
    url = settings.effective_database_url

    connect_args = {}
    if url.startswith("sqlite"):
        # SQLite does not enforce foreign keys by default; enable it per-connection.
        connect_args["check_same_thread"] = False

    engine = create_engine(url, connect_args=connect_args, echo=False)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


engine = _make_engine()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    """FastAPI dependency: yield a DB session and close it when done."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema_migrations(eng):
    """
    Ensure newly added columns exist in existing SQLite databases without requiring Alembic
    or dropping tables.
    """
    from sqlalchemy import inspect, text
    inspector = inspect(eng)
    tables = inspector.get_table_names()
    with eng.connect() as conn:
        if "calls" in tables:
            call_cols = [c["name"] for c in inspector.get_columns("calls")]
            if "transcription_mode" not in call_cols:
                conn.execute(text("ALTER TABLE calls ADD COLUMN transcription_mode VARCHAR(20) DEFAULT 'auto' NOT NULL"))
        if "transcript_segments" in tables:
            seg_cols = [c["name"] for c in inspector.get_columns("transcript_segments")]
            if "transcript_version_id" not in seg_cols:
                conn.execute(text("ALTER TABLE transcript_segments ADD COLUMN transcript_version_id INTEGER"))
        if "evaluations" in tables:
            eval_cols = [c["name"] for c in inspector.get_columns("evaluations")]
            if "transcript_version_id" not in eval_cols:
                conn.execute(text("ALTER TABLE evaluations ADD COLUMN transcript_version_id INTEGER"))
            if "is_stale" not in eval_cols:
                conn.execute(text("ALTER TABLE evaluations ADD COLUMN is_stale BOOLEAN DEFAULT 0 NOT NULL"))
            if "stale_reason" not in eval_cols:
                conn.execute(text("ALTER TABLE evaluations ADD COLUMN stale_reason VARCHAR(255)"))
        conn.commit()
