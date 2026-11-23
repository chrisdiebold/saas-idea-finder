# ruff: noqa: E501
"""Phase 2 Sources: parsing and paging, against small fakes shaped like the real responses
(probed 2026-11-21). Real pages aren't committed: they're other people's posts."""

import json
from datetime import UTC, datetime

import httpx2
import pytest

from ideafinder.collectors.base import HttpSource, SourceGone
from ideafinder.collectors.bluesky import SearchStream, Session
from ideafinder.collectors.feature_boards import (
    FeaturebaseStream,
    FeatureBoardsCollector,
    parse_canny,
    parse_uservoice,
)
from ideafinder.collectors.github import RepoStream
from ideafinder.collectors.mastodon import HashtagStream
from ideafinder.collectors.producthunt import LaunchStream, ProductHuntCollector
from ideafinder.collectors.shopify_apps import ReviewStream, parse_page
from ideafinder.collectors.zapier import TopicFeed
from ideafinder.config import QueryConfig, SourceConfig
from ideafinder.db.models import Item
from ideafinder.pipeline.prefilter import decide
from ideafinder.settings import Settings

SINCE = datetime(2026, 11, 1, tzinfo=UTC)


def http_for(handler) -> HttpSource:
    return HttpSource(httpx2.AsyncClient(transport=httpx2.MockTransport(handler)), 6000)


async def collect(stream, since=SINCE):
    return [item async for item in stream.fetch(since)]


# --- GitHub ---------------------------------------------------------------------------------


def gh_issue(n: int, updated: str, pr: bool = False) -> dict:
    issue = {
        "number": n,
        "html_url": f"https://github.com/o/r/issues/{n}",
        "title": f"Issue {n}",
        "body": "We export to CSV by hand every week.",
        "user": {"login": f"user{n}"},
        "labels": [{"name": "enhancement"}],
        "comments": 2,
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": updated,
        "reactions": {"total_count": 7, "+1": 5},
        "state": "open",
    }
    if pr:
        issue["pull_request"] = {"url": "x"}
    return issue


async def test_github_skips_pull_requests_and_counts_reactions_as_votes():
    seen = []

    def handler(request):
        seen.append(request)
        page = int(request.url.params["page"])
        if page == 1:
            issues = [gh_issue(i, "2026-11-20T00:00:00Z") for i in range(100)]
            issues[3]["pull_request"] = {"url": "x"}
            return httpx2.Response(200, json=issues)
        return httpx2.Response(200, json=[gh_issue(500, "2026-11-02T00:00:00Z")])

    stream = RepoStream(http_for(handler), "o/r", {"Authorization": "Bearer t"}, 10)
    items = await collect(stream)
    assert len(items) == 100 and all(i.external_id != "o/r#3" for i in items)
    assert items[0].votes == 7 and items[0].extra["thumbs_up"] == 5
    assert items[0].cursor_value == datetime(2026, 11, 20, tzinfo=UTC)
    assert seen[0].headers["Authorization"] == "Bearer t"
    assert seen[0].url.params["since"].startswith("2026-11-01")


async def test_github_hourly_limit_stops_the_source_politely():
    stream = RepoStream(http_for(lambda r: httpx2.Response(403, json={})), "o/r", {}, 5)
    with pytest.raises(SourceGone, match="IDEAFINDER_GITHUB_TOKEN"):
        await collect(stream)


# --- Shopify App Store ----------------------------------------------------------------------


def shopify_review(rid: int, date: str, body: str, store: str) -> str:
    return f"""<div data-merchant-review="" data-review-content-id="{rid}" class="tw-flex">
      <div aria-label="1 out of 5 stars" role="img"><svg><path d="M8"/></svg></div>
      <div class="tw-text-body-xs tw-text-fg-tertiary"> {date} </div>
      <div><p class="tw-break-words">{body}<br>Second line.</p></div>
      <div class="tw-text-heading-xs tw-text-fg-primary"><span> {store} </span></div>
      <div class="tw-text-body-xs tw-text-fg-tertiary tw-mb-sm"> Dev replied {date} </div>
      <p class="tw-break-words">Sorry to hear that!</p>
    </div>"""


def test_shopify_review_parsing_leaves_out_the_developer_reply():
    html = shopify_review(11, "November 19, 2026", "Billing is a trap.", "Fancy Steel")
    [item] = parse_page(html, "someapp", 1)
    assert item.external_id == "someapp/11" and item.author == "Fancy Steel"
    assert item.body == "Billing is a trap.\nSecond line."
    assert item.created_at == datetime(2026, 11, 19, tzinfo=UTC)
    assert item.extra == {"rating": 1}


async def test_shopify_pages_newest_first_until_older_than_cursor():
    pages = {
        1: shopify_review(1, "November 20, 2026", "a", "s1") + shopify_review(2, "November 5, 2026", "b", "s2"),
        2: shopify_review(3, "October 30, 2026", "c", "s3"),
    }  # fmt: skip
    requested = []

    def handler(request):
        page = int(request.url.params["page"])
        requested.append((page, request.url.params["ratings[]"], request.url.params["sort_by"]))
        return httpx2.Response(200, text=pages.get(page, ""))

    items = await collect(ReviewStream(http_for(handler), "someapp", 2))
    assert [i.external_id for i in items] == ["someapp/1", "someapp/2"]
    assert requested == [(1, "2", "newest"), (2, "2", "newest")]


# --- Feature boards -------------------------------------------------------------------------

USERVOICE = """<ol class="uvList uvList-ideas">
<li class="uvListItem uvIdea uvIdea-list"> <div class="uvIdeaHeader">
 <h2 class="uvIdeaTitle uvCustomLink-color"> <a href="/forums/1-invoices/suggestions/51-pro-forma"> Pro-forma &amp; deposits </a> </h2>
 <div class="uvIdeaDescription uvIdeaDescription-truncated"> <div class="full-text" style="display: none;"><div class="typeset"><p>Full text here.</p></div></div> <div class="truncated-text"><div class="typeset"><p>Full…</p></div></div> </div> </div>
 <div class="uvIdeaVoteBadge"> <div class="uvIdeaVoteCount" data-id="51"> <strong>1,116</strong> <span> votes</span> </div></div>
</li>
</ol>"""  # noqa: E501

CANNY = """<script>window.__data = {"boards":{"items":{}},"cookies":{"theme":undefined},
"posts":{"b1":{"user-segments":{"_id":"p1","title":"User segments with OR","details":"Only AND now.",
"score":58,"status":"open","commentCount":6,"urlName":"user-segments",
"board":{"_id":"b1","urlName":"feature-requests"}}}}};</script>"""


def test_uservoice_and_canny_parse_with_first_seen_dates():
    seen = datetime(2026, 11, 21, tzinfo=UTC)
    [idea] = parse_uservoice(USERVOICE, "uservoice:x", "productideas.xero.com", seen)
    assert (idea.title, idea.votes, idea.body) == ("Pro-forma & deposits", 1116, "Full text here.")
    assert idea.url == "https://productideas.xero.com/forums/1-invoices/suggestions/51-pro-forma"
    assert idea.extra["date_is_first_seen"] and idea.created_at == seen

    [post] = parse_canny(
        CANNY, "canny:feedback.canny.io/feature-requests", "feedback.canny.io", seen
    )
    assert (post.title, post.votes, post.body) == ("User segments with OR", 58, "Only AND now.")
    assert post.url == "https://feedback.canny.io/feature-requests/p/user-segments"


async def test_featurebase_pages_by_date_until_the_cursor():
    def post(slug: str, day: int) -> dict:
        return {"slug": slug, "title": slug, "content": "<p>Please add X</p>", "upvotes": 3,
                "date": f"2026-11-{day:02d}T10:00:00.000Z", "user": {"name": "Ann", "type": "user"},
                "postStatus": {"name": "Open"}, "commentCount": 1}  # fmt: skip

    def handler(request):
        assert request.url.params["sortBy"] == "date:desc"
        page = int(request.url.params["page"])
        results = [post("a", 20), post("b", 10)] if page == 1 else [post("c", 2), post("d", 1)]
        return httpx2.Response(200, json={"results": results, "totalPages": 5})

    stream = FeaturebaseStream(http_for(handler), "featurebase:fb.app", "fb.app")
    items = await collect(stream, since=datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert [i.title for i in items] == ["a", "b", "c"]  # d is before the cursor; paging stops
    assert items[0].body == "Please add X" and items[0].extra["author_is_staff"] is False


def test_feature_board_platform_must_be_known():
    source = SourceConfig(key="feature_boards", name="x", phase=2, rate_limit_per_minute=1,
                          communities=["trello:board"])  # fmt: skip
    collector = FeatureBoardsCollector(
        source,
        QueryConfig(request=[], payment=[], workaround=[], complaint=[], question=[]),
        None,
        Settings(),
    )
    with pytest.raises(ValueError, match="uservoice:"):
        collector.streams()


# --- Zapier, Mastodon, Bluesky, Product Hunt ------------------------------------------------


async def test_zapier_feed_keeps_new_topics():
    feed = b"""<rss><channel>
      <item><title>Connect Discord to Drive</title><link>https://community.zapier.com/how-do-i-3/connect-54014</link>
        <description>&lt;p&gt;Is there a way?&lt;/p&gt;</description><category>How Do I</category>
        <pubDate>Sat, 21 Nov 2026 16:31:38 +0100</pubDate></item>
      <item><title>Old</title><link>https://community.zapier.com/x-1</link><description>old</description>
        <pubDate>Sat, 03 Oct 2026 10:00:00 +0000</pubDate></item>
    </channel></rss>"""  # noqa: E501
    [item] = await collect(TopicFeed(http_for(lambda r: httpx2.Response(200, content=feed))))
    assert (item.external_id, item.body, item.extra["category"]) == (
        "54014",
        "Is there a way?",
        "How Do I",
    )


def toot(i: int, day: int, **kw) -> dict:
    status = {"id": str(1000 - i), "uri": f"https://m.s/statuses/{i}", "url": f"https://m.s/@a/{i}",
              "created_at": f"2026-11-{day:02d}T08:00:00.000Z", "content": "<p>We track this in a spreadsheet</p>",
              "account": {"acct": "ann", "bot": False}, "favourites_count": 2, "language": "en",
              "in_reply_to_id": None, "reblog": None}  # fmt: skip
    status.update(kw)
    return status


async def test_mastodon_follows_the_hashtag_back_to_the_cursor():
    pages = [
        [toot(1, 20), toot(2, 19, reblog={"id": "x"}), toot(3, 18, account={"acct": "b", "bot": True})],
        [toot(4, 10), toot(5, 1)],
    ]  # fmt: skip
    calls = []

    def handler(request):
        calls.append(request.url.params.get("max_id"))
        return httpx2.Response(200, json=pages[len(calls) - 1])

    stream = HashtagStream(http_for(handler), "m.s", "smallbusiness")
    items = await collect(stream, since=datetime(2026, 11, 1, 12, tzinfo=UTC))
    assert [i.external_id for i in items] == ["https://m.s/statuses/1", "https://m.s/statuses/4"]
    assert items[0].author == "ann@m.s" and items[0].body == "We track this in a spreadsheet"
    assert calls == [None, "997"]  # the next page starts after the last one seen


def bsky_post(rkey: str, when: str, text: str = "Is there a tool for rota planning?") -> dict:
    return {"uri": f"at://did:plc:x/app.bsky.feed.post/{rkey}", "author": {"handle": "ann.bsky.social"},
            "record": {"text": text, "createdAt": when, "langs": ["en"]}, "likeCount": 4, "replyCount": 0}  # fmt: skip


def test_bluesky_keeps_only_posts_that_say_the_phrase_and_zapier_text_is_unglued():
    from ideafinder.collectors.bluesky import contains_phrase
    from ideafinder.collectors.zapier import unglue

    assert contains_phrase("Honestly I\u2019d  pay for this", "I'd pay for")
    assert not contains_phrase("I modified an almond cake recipe", "alternative to")
    assert unglue("Hey everyone!I'm new.It fails on WooCommerce") == (
        "Hey everyone! I'm new. It fails on WooCommerce"
    )


async def test_bluesky_reads_one_page_without_an_account():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx2.Response(
            200,
            json={
                "posts": [
                    bsky_post("k1", "2026-11-20T10:00:00Z"),
                    bsky_post("k9", "2026-11-20T10:00:00Z", text="An almond cake recipe"),
                ],
                "cursor": "c2",
            },
        )

    http = http_for(handler)
    items = await collect(SearchStream(http, "is there a tool", Session(http, None, None)))
    assert len(calls) == 1 and calls[0].url.params["q"] == '"is there a tool"'
    assert (
        items[0].url == "https://bsky.app/profile/ann.bsky.social/post/k1" and items[0].votes == 4
    )


async def test_bluesky_pages_with_an_app_password():
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.path.endswith("createSession"):
            assert json.loads(request.content)["identifier"] == "me.bsky.social"
            return httpx2.Response(200, json={"accessJwt": "jwt"})
        cursor = request.url.params.get("cursor")
        posts = (
            [bsky_post("k1", "2026-11-20T10:00:00Z")]
            if not cursor
            else [bsky_post("k2", "2026-11-05T10:00:00Z")]
        )
        return httpx2.Response(200, json={"posts": posts, "cursor": None if cursor else "c2"})

    http = http_for(handler)
    session = Session(http, "me.bsky.social", "pw")
    items = await collect(SearchStream(http, "is there a tool", session))
    assert [i.external_id.rsplit("/", 1)[-1] for i in items] == ["k1", "k2"]
    assert calls[1].headers["Authorization"] == "Bearer jwt"


async def test_product_hunt_needs_a_token_and_launches_go_to_market():
    source = SourceConfig(key="producthunt", name="PH", phase=2, rate_limit_per_minute=1)
    with pytest.raises(SourceGone, match="IDEAFINDER_PRODUCTHUNT_TOKEN"):
        ProductHuntCollector(
            source,
            QueryConfig(request=[], payment=[], workaround=[], complaint=[], question=[]),
            None,
            Settings(producthunt_token=None),
        ).streams()

    node = {"id": "1", "name": "Crewly", "tagline": "Rota planning for cafes", "description": "d",
            "url": "https://www.producthunt.com/posts/crewly", "website": "https://crewly.app",
            "votesCount": 210, "commentsCount": 4, "createdAt": "2026-11-20T08:00:00Z",
            "topics": {"edges": [{"node": {"name": "Productivity"}}]}}  # fmt: skip

    def handler(request):
        assert request.headers["Authorization"] == "Bearer t"
        body = json.loads(request.content)
        assert body["variables"]["postedAfter"].startswith("2026-11-01")
        assert "featured: true" in body["query"]  # ~600 launches a day otherwise
        return httpx2.Response(200, json={"data": {"posts": {
            "edges": [{"node": node}], "pageInfo": {"hasNextPage": False, "endCursor": None}}}})  # fmt: skip

    [launch] = await collect(LaunchStream(http_for(handler), "t"))
    assert launch.title == "Crewly: Rota planning for cafes" and launch.extra["topics"] == [
        "Productivity"
    ]
    item = Item(kind=launch.kind, title=launch.title, body=launch.body, extra=launch.extra)
    assert decide(item).outcome == "market"
