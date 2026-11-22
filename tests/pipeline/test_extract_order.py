"""Extraction order: Sources take turns, so one big backlog can't hold up the rest."""

from datetime import UTC, datetime

import pytest

from ideafinder.db.models import Community, Item, Source
from ideafinder.pipeline.extract import pending_chunks

pytestmark = pytest.mark.db


async def test_sources_take_turns_and_can_be_chosen(session):
    n = 0
    for key, threads in (("jira_atlassian", 5), ("hackernews", 2), ("github", 1)):
        source = Source(key=key, name=key, phase=1, enabled=True)
        session.add(source)
        await session.flush()
        community = Community(source_id=source.id, external_ref=key, name=key)
        session.add(community)
        await session.flush()
        for t in range(threads):
            n += 1
            session.add(
                Item(source_id=source.id, community_id=community.id, external_id=str(n),
                     thread_external_id=f"{key}-{t}", kind="post", url="u", body="text",
                     language="en", raw={}, prefilter="keep",
                     created_at=datetime(2026, 1, 1, tzinfo=UTC))
            )  # fmt: skip
    await session.commit()

    chunks = await pending_chunks(session, 4)
    assert [c.source_key for c in chunks] == [
        "jira_atlassian",
        "hackernews",
        "github",
        "jira_atlassian",
    ]
    only = await pending_chunks(session, 10, ["hackernews", "github"])
    assert sorted(c.source_key for c in only) == ["github", "hackernews", "hackernews"]


async def test_a_thread_extracted_twice_is_stored_once(session, evidence):
    from sqlalchemy import func, select

    from ideafinder.db.models import Signal
    from ideafinder.pipeline.extract import Chunk, ParsedSignal, store_signals

    item = await evidence.item("ann")
    chunk = Chunk("hackernews", None, [item])
    parsed = [ParsedSignal(item, "pain", "p", "persona", 3, "text", True, None, [])]
    assert await store_signals(session, chunk, parsed, "m") == 1
    await session.commit()
    # A second extraction that picked the same Thread finishes later: nothing is added.
    assert await store_signals(session, chunk, parsed, "m") == 0
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(Signal)) == 1
