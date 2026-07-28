from __future__ import annotations

from contextlib import contextmanager
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings


def normalize_database_url(url: str) -> str:
    """Render often provides postgres://. SQLAlchemy 2/psycopg wants postgresql+psycopg://."""
    url = (url or '').strip() or 'sqlite:///./kaspi_saas.db'
    if url.startswith('postgres://'):
        url = 'postgresql+psycopg://' + url[len('postgres://'):]
    elif url.startswith('postgresql://') and '+psycopg' not in url:
        url = 'postgresql+psycopg://' + url[len('postgresql://'):]
    return url


DATABASE_URL = normalize_database_url(settings.DATABASE_URL)
connect_args: dict = {}
engine_kwargs: dict = {'pool_pre_ping': True}

if DATABASE_URL.startswith('sqlite'):
    connect_args = {'check_same_thread': False}
else:
    # Render Postgres can close idle connections; recycling avoids stale pool issues.
    engine_kwargs.update({'pool_size': 5, 'max_overflow': 5, 'pool_recycle': 1800})

engine = create_engine(DATABASE_URL, connect_args=connect_args, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@contextmanager
def db_session():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    import app.models  # noqa: F401 - register models
    Base.metadata.create_all(bind=engine)
    # create_all does not add new columns to existing tables. This keeps old SQLite/PG data safe.
    try:
        from app.core.schema_upgrade import run_safe_schema_upgrade
        run_safe_schema_upgrade(engine)
    except Exception as exc:  # pragma: no cover - do not block startup because of best-effort upgrade
        import logging
        logging.getLogger(__name__).warning('Safe schema upgrade skipped: %s', exc)
