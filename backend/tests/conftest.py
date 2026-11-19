"""Test configuration.

Integration tests use a real PostgreSQL database (``TEST_DATABASE_URL``,
default ``georisk_test`` on localhost) — the schema relies on PostgreSQL
features (JSONB, arrays, ON CONFLICT). Fixtures under ``tests/fixtures`` are
small captured samples of real GDELT/UCDP data, used only by tests.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

os.environ.setdefault("TEST_DATABASE_URL", "postgresql+psycopg://georisk@localhost:5432/georisk_test")
os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
os.environ["ENVIRONMENT"] = "test"
os.environ["AI_PROVIDER"] = "none"
os.environ["LOG_JSON"] = "false"
os.environ["EXPORT_DIR"] = str(Path(__file__).parent / ".artifacts" / "exports")
os.environ["MODEL_DIR"] = str(Path(__file__).parent / ".artifacts" / "models")
os.environ.setdefault("API_KEYS", "")

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.db import Base, SessionLocal, get_engine  # noqa: E402
from app.ingestion import http  # noqa: E402
from app.ingestion.sources import seed_sources  # noqa: E402
from app.registry.service import seed_registry  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
FIXTURE_NOW = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)
REGISTRY_TABLES = {"regions", "countries", "country_aliases", "country_groups", "country_group_members", "organizations",
                   "source_code_mappings", "data_sources", "source_permissions"}


@pytest.fixture(scope="session")
def engine():
    eng = get_engine()
    with eng.begin() as c:
        c.execute(text("DROP SCHEMA public CASCADE; CREATE SCHEMA public;"))
    Base.metadata.create_all(eng)
    with SessionLocal() as s:
        seed_registry(s)
        seed_sources(s)
        s.commit()
    return eng


@pytest.fixture
def db(engine):
    tables = [t for t in Base.metadata.sorted_tables if t.name not in REGISTRY_TABLES]
    with engine.begin() as c:
        c.execute(text("TRUNCATE " + ", ".join(t.name for t in tables) + " RESTART IDENTITY CASCADE"))
        c.execute(text("UPDATE source_permissions SET allow_export = true WHERE data_source_id IN "
                       "(SELECT id FROM data_sources WHERE key IN ('gdelt_events','gdelt_doc','ucdp_ged'))"))
    s = SessionLocal()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


class FixtureFetcher:
    """Serves GDELT files from tests/fixtures; anything else is a 404."""

    def __init__(self):
        self.calls: list[str] = []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        name = url.rsplit("/", 1)[-1]
        path = FIXTURES / name
        if not path.exists():
            raise http.NotFoundError(url)
        return path.read_bytes()


@pytest.fixture
def fetcher():
    return FixtureFetcher()


@pytest.fixture
def ingested(db, fetcher):
    """Database with both GDELT fixture batches ingested."""
    from app.ingestion.connectors.gdelt_events import GdeltEventsConnector

    c = GdeltEventsConnector(db, fetcher=fetcher)
    c.run()
    return db


@pytest.fixture
def analyst_key(monkeypatch):
    """Configure API keys for the duration of a test; returns analyst headers."""
    from app.core.config import get_settings

    monkeypatch.setenv("API_KEYS", "admin:adm-secret,analyst:ana-secret")
    get_settings.cache_clear()
    yield {"X-API-Key": "ana-secret"}
    get_settings.cache_clear()
