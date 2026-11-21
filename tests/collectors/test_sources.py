"""Per-Source parsing and paging, against fakes shaped like the real APIs."""

from datetime import UTC, datetime

import httpx2
import pytest

from ideafinder.collectors.base import HttpSource, SourceGone
from ideafinder.collectors.hackernews import SearchStream, parse_hit
from ideafinder.collectors.jira import ProjectStream, parse_issue
from ideafinder.collectors.stackexchange import QuestionStream, parse_question
from ideafinder.collectors.wordpress import FeedStream
from tests.collectors.fakes import (
    T0,
    hn_comment,
    hn_page,
    hn_story,
    jira_issue,
    se_page,
    se_question,
    wp_feed,
)

EPOCH = datetime(2020, 1, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    slept: list[float] = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("asyncio.sleep", fake_sleep)
    return slept


def http_for(handler) -> HttpSource:
    return HttpSource(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), 6000)


async def collect(stream, since=EPOCH):
    return [item async for item in stream.fetch(since)]


# --- Hacker News ---------------------------------------------------------------------------


def test_hn_story_and_comment_parsing():
    story = parse_hit(hn_story("100", T0, ("story", "ask_hn")))
    assert story.kind == "post" and story.votes == 12 and story.thread_external_id == "100"
    assert story.body == "We do this by hand & it takes hours."
    assert story.extra["ask_hn"] is True
    assert story.url == "https://news.ycombinator.com/item?id=100"

    comment = parse_hit(hn_comment("101", T0, "100"))
    assert comment.kind == "comment" and comment.thread_external_id == "100"
    assert comment.body == "I'd pay for this.\nWe currently use a spreadsheet."


async def test_hn_walks_backwards_past_the_1000_hit_cap():
    # 1,500 matching hits: Algolia only pages through the newest 1,000, so the stream must
    # narrow the upper bound and search again to reach the rest.
    hits = [hn_story(str(i), T0 - i) for i in range(1500)]

    def handler(request):
        p = request.url.params
        page, per = int(p["page"]), int(p["hitsPerPage"])
        lo, *hi = p["numericFilters"].split(",")
        upper = int(hi[0].split("<=")[1]) if hi else None
        matching = [h for h in hits if upper is None or h["created_at_i"] <= upper]
        reachable = matching[:1000]
        return httpx2.Response(
            200, json=hn_page(reachable[page * per : (page + 1) * per], len(matching), page, per)
        )

    items = await collect(SearchStream(http_for(handler), "q", {"query": "x"}))
    assert len({i.external_id for i in items}) == 1500
    assert len(items) == 1500  # the overlap at the boundary isn't yielded twice


# --- Jira ----------------------------------------------------------------------------------


def test_jira_issue_and_comments():
    items = parse_issue(jira_issue(comments=2), "JRACLOUD")
    issue, *comments = items
    assert issue.kind == "issue" and issue.votes == 412 and issue.author == "Carol"
    assert issue.url == "https://jira.atlassian.com/browse/JRACLOUD-1"
    assert issue.cursor_value == datetime(2026, 11, 20, 9, tzinfo=UTC)  # follows "updated"
    assert [c.external_id for c in comments] == [
        "JRACLOUD-1/comment/1000",
        "JRACLOUD-1/comment/1001",
    ]
    assert all(c.thread_external_id == "JRACLOUD-1" for c in comments)


async def test_jira_pages_until_total():
    seen_jql = []

    def handler(request):
        seen_jql.append(request.url.params["jql"])
        start = int(request.url.params["startAt"])
        issues = [jira_issue(f"JRACLOUD-{start + i}", comments=0) for i in range(2)]
        return httpx2.Response(
            200, json={"startAt": start, "total": 5, "issues": issues[: 5 - start]}
        )

    items = await collect(ProjectStream(http_for(handler), "JRACLOUD"))
    assert len(items) == 5
    assert "type = Suggestion AND resolution = Unresolved" in seen_jql[0]


# --- WordPress.org -------------------------------------------------------------------------


async def test_wordpress_keeps_only_low_ratings_and_cleans_text():
    feed = wp_feed(
        [
            ("bad", "Broken again", 2, "Fri, 20 Nov 2026 10:00:00 +0000"),
            ("good", "Love it", 5, "Fri, 20 Nov 2026 11:00:00 +0000"),
            ("old", "Ancient", 1, "Mon, 01 Jan 2024 10:00:00 +0000"),
        ]
    )
    http = http_for(lambda r: httpx2.Response(200, content=feed))
    items = await collect(
        FeedStream(http, "woocommerce", is_review=True, max_rating=3),
        since=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert [i.title for i in items] == ["Broken again"]
    review = items[0]
    assert review.kind == "review" and review.extra == {"rating": 2, "replies": 2}
    assert review.body.startswith("The plugin keeps breaking")
    assert "Rating:" not in review.body and "Replies:" not in review.body


# --- Stack Exchange ------------------------------------------------------------------------


def test_stackexchange_question_parsing():
    q = parse_question(se_question(42, T0), "softwarerecs")
    assert q.external_id == "softwarerecs:42" and q.community == "softwarerecs"
    assert q.title == 'Tool to send "invoice" reminders automatically?'
    assert q.author == "Erin Tür" and q.votes == 4 and q.extra["view_count"] == 250


async def test_stackexchange_pages_sends_key_and_honours_backoff(no_sleep):
    requests = []

    def handler(request):
        requests.append(request.url.params)
        page = int(request.url.params["page"])
        return httpx2.Response(
            200,
            json=se_page(
                [se_question(page, T0)], has_more=page < 2, backoff=10 if page == 1 else None
            ),
        )

    stream = QuestionStream(http_for(handler), "softwarerecs", "k", "questions", {}, "SECRET")
    items = await collect(stream)
    assert [i.external_id for i in items] == ["softwarerecs:1", "softwarerecs:2"]
    assert all(p["key"] == "SECRET" for p in requests)
    assert 10 in no_sleep


async def test_stackexchange_stops_before_quota_runs_out():
    http = http_for(lambda r: httpx2.Response(200, json=se_page([se_question(1, T0)], True, 3)))
    stream = QuestionStream(http, "softwarerecs", "k", "questions", {}, None)
    with pytest.raises(SourceGone, match="quota"):
        await collect(stream)
