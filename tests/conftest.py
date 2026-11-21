import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

from ideafinder.db.engine import get_engine
from ideafinder.settings import PROJECT_ROOT, Settings, get_settings

TEST_DB = "ideafinder_test"
SERVER_URL = os.environ.get("IDEAFINDER_TEST_SERVER_URL", "postgresql://localhost:5432")
UNREACHABLE_URL = "postgresql://localhost:1/ideafinder"  # nothing listens on port 1


@pytest.fixture
def repo_config_dir() -> Path:
    return PROJECT_ROOT / "config"


@pytest.fixture
def use_settings(monkeypatch):
    """Point the app at a given database URL for one test."""

    def _use(database_url: str) -> Settings:
        monkeypatch.setenv("IDEAFINDER_DATABASE_URL", database_url)
        get_settings.cache_clear()
        get_engine.cache_clear()
        return get_settings()

    yield _use
    get_settings.cache_clear()
    get_engine.cache_clear()


def _server_reachable() -> bool:
    try:
        with psycopg.connect(f"{SERVER_URL}/postgres", connect_timeout=2):
            return True
    except psycopg.OperationalError:
        return False


@pytest.fixture(scope="session")
def test_database_url() -> Iterator[str]:
    """A fresh, empty database for the session; dropped afterwards. Skips if Postgres is down."""
    if not _server_reachable():
        pytest.skip(f"Postgres not reachable at {SERVER_URL}")
    _drop_test_database()
    yield f"{SERVER_URL}/{TEST_DB}"
    _drop_test_database()


def _drop_test_database() -> None:
    drop = sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(TEST_DB))
    with psycopg.connect(f"{SERVER_URL}/postgres", autocommit=True) as conn:
        conn.execute(drop)
