"""End-to-end collection against fake Sources and a real (test) database."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text

from ideafinder.collectors.runner import collect
from ideafinder.config import load_config
from ideafinder.db import admin
from tests.collectors.fakes import all_sources_transport

pytestmark = pytest.mark.db


@pytest.fixture
def settings(use_settings, test_database_url):
    s = use_settings(test_database_url)
    admin.create_database_if_missing(s)
    admin.upgrade(s)
    engine = create_engine(s.sqlalchemy_url)
    with engine.begin() as conn:
        conn.execute(text("TRUNCATE items, communities, collector_cursors, runs, sources CASCADE"))
    engine.dispose()
    return s


@pytest.fixture
def config(repo_config_dir):
    full = load_config(repo_config_dir)
    # One query and one Community per Source keeps the fake small.
    queries = full.sources.queries.model_copy(
        update={
            "request": ["is there a tool"],
            "payment": [],
            "workaround": [],
            "complaint": [],
            "question": [],
        }
    )
    sources = [
        s.model_copy(update={"communities": s.communities[:1]}) for s in full.sources.sources
    ]
    return full.model_copy(
        update={"sources": full.sources.model_copy(update={"queries": queries, "sources": sources})}
    )


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    async def fake_sleep(seconds):
        return None

    monkeypatch.setattr("ideafinder.collectors.base.asyncio.sleep", fake_sleep)


def query(settings, sql):
    engine = create_engine(settings.sqlalchemy_url)
    with engine.connect() as conn:
        rows = conn.execute(text(sql)).all()
    engine.dispose()
    return rows


async def test_collects_every_source_and_records_runs(settings, config):
    reports = await collect(config, settings, transport=all_sources_transport())
    assert {r.source: r.status for r in reports} == {
        "hackernews": "succeeded",
        "jira_atlassian": "succeeded",
        "wordpress": "succeeded",
        "stackexchange": "succeeded",
    }
    by_source = dict(
        query(
            settings,
            """
        SELECT s.key, count(*) FROM items i JOIN sources s ON s.id = i.source_id GROUP BY s.key
    """,
        )
    )
    # HN: story + comment + Show HN · Jira: issue + comment · WordPress: 2★ review + English
    # support post (5★ review and Spanish post dropped) · Stack Exchange: one question
    assert by_source == {"hackernews": 3, "jira_atlassian": 2, "wordpress": 2, "stackexchange": 1}

    wp = next(r for r in reports if r.source == "wordpress")
    assert wp.totals.skipped_language == 1

    communities = query(settings, "SELECT external_ref, watch_status FROM communities ORDER BY 1")
    assert ("woocommerce", "watched") in communities and ("hackernews", "watched") in communities

    runs = query(settings, "SELECT status, count(*) FROM runs WHERE stage = 'collect' GROUP BY 1")
    assert runs == [("succeeded", 4)]
    assert query(settings, "SELECT count(*) FROM collector_cursors")[0][0] > 0


async def test_second_run_finds_nothing_new(settings, config):
    await collect(config, settings, transport=all_sources_transport())
    reports = await collect(config, settings, transport=all_sources_transport())
    assert sum(r.totals.new for r in reports) == 0
    assert sum(r.totals.updated for r in reports) > 0


async def test_trial_window_does_not_save_cursors(settings, config):
    since = datetime.now(UTC) - timedelta(days=3)
    await collect(config, settings, since_override=since, transport=all_sources_transport())
    assert query(settings, "SELECT count(*) FROM collector_cursors")[0][0] == 0
    assert query(settings, "SELECT count(*) FROM items")[0][0] > 0


async def test_a_failing_source_does_not_stop_the_others(settings, config):
    transport = all_sources_transport(fail_hosts=frozenset({"jira.atlassian.com"}))
    reports = {r.source: r for r in await collect(config, settings, transport=transport)}
    assert reports["jira_atlassian"].status == "failed"
    assert "500" in reports["jira_atlassian"].errors[0]
    assert reports["hackernews"].status == "succeeded"
    cursors = query(
        settings,
        """
        SELECT s.key FROM collector_cursors c JOIN sources s ON s.id = c.source_id
    """,
    )
    assert ("jira_atlassian",) not in cursors  # it will retry from the same point next run


async def test_unknown_source_rejected(settings, config):
    with pytest.raises(ValueError, match="unknown or disabled"):
        await collect(config, settings, source_keys=["myspace"])


async def test_status_page_reports_latest_run_per_source(settings, config):
    from ideafinder.status import get_status

    await collect(
        config,
        settings,
        transport=all_sources_transport(fail_hosts=frozenset({"jira.atlassian.com"})),
    )
    runs = get_status(settings).source_runs
    assert runs["hackernews"].status == "succeeded" and runs["hackernews"].items_total == 3
    assert runs["jira_atlassian"].status == "failed" and runs["jira_atlassian"].errors
