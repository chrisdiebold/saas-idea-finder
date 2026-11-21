"""Running it daily: the chain, backups, the launchd jobs, Health and This Week."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from sqlalchemy import create_engine, text

from ideafinder import backup as backup_mod
from ideafinder import health as health_mod
from ideafinder.config import load_config
from ideafinder.daily import LOCK_KEY, run_daily
from ideafinder.db.engine import get_engine
from ideafinder.schedule import JOBS, plist_for
from ideafinder.web.app import app
from tests.collectors.fakes import all_sources_transport

NOW = datetime(2026, 11, 21, 6, 0, tzinfo=UTC)


def touch(directory: Path, when: datetime) -> Path:
    path = directory / f"ideafinder-{when:%Y%m%d-%H%M%S}.dump"
    path.write_bytes(b"x")
    return path


# --- no database needed ---------------------------------------------------------------------


def test_prune_keeps_14_days_by_name_and_never_the_newest(tmp_path):
    keep = touch(tmp_path, NOW - timedelta(days=13))
    old = touch(tmp_path, NOW - timedelta(days=15))
    (tmp_path / "notes.txt").write_text("not a backup")
    assert backup_mod.prune(tmp_path, 14, NOW) == [old]
    assert keep.exists() and (tmp_path / "notes.txt").exists()

    lonely = tmp_path / "lonely"
    lonely.mkdir()
    only = touch(lonely, NOW - timedelta(days=90))
    assert backup_mod.prune(lonely, 14, NOW) == [] and only.exists()


def test_backups_listed_newest_first(tmp_path):
    a = touch(tmp_path, NOW - timedelta(days=2))
    b = touch(tmp_path, NOW - timedelta(days=1))
    assert [x.path for x in backup_mod.list_backups(tmp_path)] == [b, a]
    assert backup_mod.list_backups(tmp_path / "missing") == []


def test_launchd_jobs_run_the_cli_with_absolute_paths(use_settings):
    settings = use_settings("postgresql://localhost:5432/ideafinder")
    by_command = {j.command: plist_for(j, settings, uv="/opt/uv/bin/uv") for j in JOBS}
    daily = by_command["daily"]
    assert daily["ProgramArguments"][0] == "/opt/uv/bin/uv"
    assert daily["ProgramArguments"][-2:] == ["ideafinder", "daily"]
    assert daily["StartCalendarInterval"] == {"Hour": 6, "Minute": 0}
    assert daily["StandardOutPath"].endswith("logs/daily.log")
    assert "/opt/uv/bin" in daily["EnvironmentVariables"]["PATH"]
    assert by_command["backup"]["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}


# --- against Postgres -----------------------------------------------------------------------


@pytest.fixture
def daily_setup(use_settings, test_database_url, tmp_path, repo_config_dir):
    from ideafinder.db import admin

    settings = use_settings(test_database_url)
    admin.create_database_if_missing(settings)
    admin.upgrade(settings)
    engine = create_engine(settings.sqlalchemy_url)
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE items, communities, collector_cursors, runs, sources, ideas CASCADE")
        )
    engine.dispose()
    settings = settings.model_copy(
        update={"backup_dir": tmp_path / "backups", "daily_extract_minutes": 1}
    )
    full = load_config(repo_config_dir)
    sources = [
        s.model_copy(update={"communities": s.communities[:1]})
        for s in full.sources.sources
        if s.phase == 1  # the fakes cover Phase 1 Sites; Phase 2 has its own tests
    ]
    queries = full.sources.queries.model_copy(
        update={"request": ["is there a tool"], "payment": [], "workaround": [], "complaint": [],
                "question": []}
    )  # fmt: skip
    config = full.model_copy(
        update={"sources": full.sources.model_copy(update={"queries": queries, "sources": sources})}
    )
    return settings, config


def lm_studio_down():
    def refuse(request):
        raise httpx2.ConnectError("refused")

    return httpx2.MockTransport(refuse)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("ideafinder.collectors.base.asyncio.sleep", fake_sleep)


@pytest.mark.db
async def test_daily_collects_and_skips_model_steps_when_lm_studio_is_down(daily_setup):
    settings, config = daily_setup
    report = await run_daily(
        settings, config, now=NOW, collect_transport=all_sources_transport(),
        llm_transport=lm_studio_down(),
    )  # fmt: skip
    assert report.status == "partial"
    assert set(report.steps["collect"].values()) == {"succeeded"}
    assert sum(report.steps["prefilter"].values()) > 0
    assert any("model steps skipped" in e for e in report.errors)

    again = await run_daily(settings, config, now=NOW + timedelta(hours=2))
    assert again.skipped.startswith("already ran")
    forced = await run_daily(
        settings, config, force=True, now=NOW, collect_transport=all_sources_transport(),
        llm_transport=lm_studio_down(),
    )  # fmt: skip
    assert forced.skipped is None

    engine = create_engine(settings.sqlalchemy_url)
    with engine.connect() as conn:
        runs = conn.execute(text("SELECT status FROM runs WHERE stage = 'daily'")).scalars().all()
    engine.dispose()
    assert runs == ["partial", "partial"]


@pytest.mark.db
async def test_daily_runs_never_overlap(daily_setup):
    settings, config = daily_setup
    async with get_engine().connect() as other:
        await other.execute(text("SELECT pg_advisory_lock(:k)"), {"k": LOCK_KEY})
        report = await run_daily(settings, config, now=NOW)
        await other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
    assert report.skipped == "another daily run is in progress"


@pytest.mark.db
def test_backup_makes_a_restorable_dump_and_records_it(daily_setup):
    settings, _ = daily_setup
    try:
        backup_mod.pg_dump_path(settings)
    except backup_mod.BackupError:
        pytest.skip("pg_dump isn't installed here")
    made, removed = backup_mod.backup(settings, now=NOW)
    assert made.path.exists() and made.size > 0 and removed == []
    assert made.path.read_bytes()[:5] == b"PGDMP"  # pg_dump's custom format
    assert not list(settings.backup_dir.glob("*.partial"))
    engine = create_engine(settings.sqlalchemy_url)
    with engine.connect() as conn:
        status = conn.execute(text("SELECT status FROM runs WHERE stage = 'backup'")).scalar()
    engine.dispose()
    assert status == "succeeded"


@pytest.mark.db
async def test_health_explains_what_is_missing(daily_setup, monkeypatch):
    settings, config = daily_setup
    monkeypatch.setattr(health_mod, "_lm_studio", lambda config: (False, []))
    h = health_mod.get_health(settings, config, now=NOW)
    text_ = " ".join(h.warnings)
    assert "ideafinder daily" in text_ and "ideafinder backup" in text_
    assert "lms server start" in text_
    assert h.latest_backup is None

    monkeypatch.setattr(
        health_mod, "_lm_studio", lambda config: (True, ["text-embedding-nomic-embed-text-v1.5"])
    )
    h = health_mod.get_health(settings, config, now=NOW)
    assert any("lms get qwen/qwen3.6-35b-a3b" in w for w in h.warnings)


@pytest.mark.db
async def test_this_week_and_health_pages_render(daily_setup, session, evidence, monkeypatch):
    monkeypatch.setattr(health_mod, "_lm_studio", lambda config: (False, []))
    idea = await evidence.idea([await evidence.signal(None, "a")], state="inbox", title="Fresh")
    idea.published_at = datetime.now(UTC)
    await session.commit()
    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app), base_url="http://t"
    ) as c:
        week = (await c.get("/week")).text
        assert "Fresh" in week and "New in the Inbox" in week
        health = await c.get("/health")
        assert health.status_code == 200 and "Daily routine" in health.text
        assert "lms server start" in health.text
        assert (await c.get("/status")).status_code == 200
