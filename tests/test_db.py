"""Integration tests against a real Postgres (skipped if it isn't running)."""

import pytest
from fastapi.testclient import TestClient
from procrastinate import PsycopgConnector
from sqlalchemy import create_engine, text

from ideafinder.db import admin
from ideafinder.status import get_status
from ideafinder.web.app import app as web_app
from ideafinder.worker import COLLECT_QUEUE, MODEL_QUEUE, heartbeat_collect, heartbeat_model
from ideafinder.worker import app as worker_app

pytestmark = pytest.mark.db


@pytest.fixture
def migrated(use_settings, test_database_url):
    settings = use_settings(test_database_url)
    admin.create_database_if_missing(settings)
    admin.upgrade(settings)
    return settings


def test_migrations_reach_head_with_pgvector(migrated):
    status = admin.status(migrated)
    assert status.up_to_date
    assert status.pgvector_version is not None


def test_upgrade_is_idempotent(migrated):
    assert admin.create_database_if_missing(migrated) is False
    admin.upgrade(migrated)
    assert admin.status(migrated).up_to_date


def test_procrastinate_schema_installed(migrated):
    engine = create_engine(migrated.sqlalchemy_url)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT to_regclass('procrastinate_jobs')")).scalar()
    engine.dispose()


async def test_worker_runs_jobs_on_both_queues_and_records_heartbeats(migrated):
    with worker_app.replace_connector(PsycopgConnector(conninfo=migrated.database_url)):
        async with worker_app.open_async():
            await heartbeat_collect.defer_async(timestamp=0)
            await heartbeat_model.defer_async(timestamp=0)
            # wait=False: process what's queued, then return.
            await worker_app.run_worker_async(
                queues=[COLLECT_QUEUE, MODEL_QUEUE], wait=False, install_signal_handlers=False
            )

    queues = {q.queue: q for q in get_status(migrated).queues}
    assert queues[COLLECT_QUEUE].alive
    assert queues[MODEL_QUEUE].alive


def test_status_page_all_clear_when_running(migrated):
    # Heartbeats were recorded by the previous test within the last few seconds.
    response = TestClient(web_app).get("/status")
    assert "up to date" in response.text
