"""Public feature-request boards (Canny, UserVoice, Featurebase), from their public pages.

Each Watch-listed board is written as "platform:address", e.g.
  uservoice:productideas.xero.com/forums/967115-invoices-quotes
  canny:feedback.canny.io/feature-requests
  featurebase:feedback.featurebase.app
Votes are the board's own vote counts.

What each platform allows (docs/research/source-access.md):
- Featurebase: the portal's own JSON (undocumented), with dates, newest first, fully paged.
- UserVoice: server-rendered pages, newest first, paged. The list shows no dates, so an idea's
  date is when it was first collected.
- Canny: the page embeds its first posts as data; there's no way to page further without the
  board owner's key, and no dates. Daily collection of the newest posts keeps up with most boards.
Undated Items are marked `extra.date_is_first_seen`, so a backfill doesn't look like a sudden
burst of activity: they're dated at the start of the backfill window instead of today.
"""

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from html import unescape

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream
from ideafinder.collectors.text import html_to_text

FEATUREBASE_PAGE = 100
USERVOICE_MAX_PAGES = 60
RECENT = timedelta(days=7)  # a cursor newer than this means "daily run", not backfill


def _undated(since: datetime) -> datetime:
    """When an undated idea is first seen: today on daily runs, the window's start on backfill."""
    now = datetime.now(UTC)
    return now if now - since < RECENT else since


# --- Featurebase ----------------------------------------------------------------------------


def parse_featurebase(post: dict, board: str, host: str) -> RawItem:
    return RawItem(
        external_id=f"{host}/{post['slug']}",
        community=board,
        kind="post",
        url=f"https://{host}/p/{post['slug']}",
        title=post.get("title"),
        body=html_to_text(post.get("content") or ""),
        created_at=datetime.fromisoformat(post["date"].replace("Z", "+00:00")),
        author=(post.get("user") or {}).get("name"),
        votes=post.get("upvotes"),
        thread_external_id=f"{host}/{post['slug']}",
        extra={
            "status": (post.get("postStatus") or {}).get("name"),
            "comments": post.get("commentCount"),
            "author_is_staff": (post.get("user") or {}).get("type") == "admin",
        },
        raw={k: post.get(k) for k in ("slug", "upvotes", "date", "commentCount")},
    )


class FeaturebaseStream(Stream):
    def __init__(self, http: HttpSource, board: str, host: str) -> None:
        self.http, self.board, self.host = http, board, host
        self.key = f"featurebase:{host}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        page = 1
        while True:
            data = await self.http.get_json(
                f"https://{self.host}/api/v1/submission",
                {"page": page, "limit": FEATUREBASE_PAGE, "sortBy": "date:desc"},
            )
            items = [parse_featurebase(p, self.board, self.host) for p in data.get("results", [])]
            for item in items:
                if item.created_at >= since:
                    yield item
            if not items or items[-1].created_at < since or page >= data.get("totalPages", 0):
                return
            page += 1


# --- UserVoice ------------------------------------------------------------------------------

_UV_IDEA = re.compile(
    r'<li class="uvListItem uvIdea uvIdea-list">(.*?)(?=<li class="uvListItem|</ol>)', re.S
)
_UV_LINK = re.compile(r'<h2 class="uvIdeaTitle[^"]*">\s*<a href="([^"#]+)">\s*(.*?)\s*</a>', re.S)
_UV_FULL = re.compile(
    r'<div class="full-text"[^>]*>(.*?)</div>\s*<div class="truncated-text"', re.S
)
_UV_DESC = re.compile(r'<div class="uvIdeaDescription[^"]*">(.*?)</div>\s*</div>', re.S)
_UV_VOTES = re.compile(r'<div class="uvIdeaVoteCount"[^>]*>\s*<strong>([\d,]+)</strong>')
_UV_ID = re.compile(r"/suggestions/(\d+)")


def parse_uservoice(html: str, board: str, host: str, seen_at: datetime) -> list[RawItem]:
    items = []
    for block in _UV_IDEA.findall(html):
        link = _UV_LINK.search(block)
        if not link:
            continue
        path, title = link.group(1), unescape(re.sub(r"<[^>]+>", "", link.group(2))).strip()
        body_html = _UV_FULL.search(block) or _UV_DESC.search(block)
        votes = _UV_VOTES.search(block)
        idea_id = _UV_ID.search(path).group(1)
        items.append(
            RawItem(
                external_id=f"{host}/{idea_id}",
                community=board,
                kind="post",
                url=f"https://{host}{path}",
                title=title,
                body=html_to_text(body_html.group(1)) if body_html else "",
                created_at=seen_at,
                votes=int(votes.group(1).replace(",", "")) if votes else None,
                thread_external_id=f"{host}/{idea_id}",
                extra={"date_is_first_seen": True},
                raw={"path": path},
            )
        )
    return items


class UserVoiceStream(Stream):
    def __init__(self, http: HttpSource, board: str, url: str) -> None:
        self.http, self.board = http, board
        self.host, _, self.path = url.partition("/")
        self.key = f"uservoice:{url}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        seen_at = _undated(since)
        # Daily runs only need the newest page or two; the first run walks the whole board.
        max_pages = 2 if seen_at > since else USERVOICE_MAX_PAGES
        for page in range(1, max_pages + 1):
            response = await self.http.get(
                f"https://{self.host}/{self.path}", {"filter": "new", "page": page}
            )
            items = parse_uservoice(response.text, self.board, self.host, seen_at)
            for item in items:
                yield item
            if not items:
                return


# --- Canny ----------------------------------------------------------------------------------

_CANNY_DATA = re.compile(r"__data\s*=\s*")
_JS_UNDEFINED = re.compile(r":undefined([,}\]])")


def parse_canny(html: str, board: str, host: str, seen_at: datetime) -> list[RawItem]:
    match = _CANNY_DATA.search(html)
    if not match:
        return []
    data, _ = json.JSONDecoder().raw_decode(_JS_UNDEFINED.sub(r":null\1", html[match.end() :]))
    items = []
    for posts in (data.get("posts") or {}).values():
        for post in posts.values():
            if not isinstance(post, dict) or not post.get("title"):
                continue
            board_slug = (post.get("board") or {}).get("urlName", "")
            items.append(
                RawItem(
                    external_id=f"{host}/{post['_id']}",
                    community=board,
                    kind="post",
                    url=f"https://{host}/{board_slug}/p/{post['urlName']}",
                    title=post["title"],
                    body=(post.get("details") or "").strip(),
                    created_at=seen_at,
                    votes=post.get("score"),
                    thread_external_id=f"{host}/{post['_id']}",
                    extra={
                        "status": post.get("status"),
                        "comments": post.get("commentCount"),
                        "date_is_first_seen": True,
                    },
                    raw={k: post.get(k) for k in ("_id", "urlName", "score", "status")},
                )
            )
    return items


class CannyStream(Stream):
    def __init__(self, http: HttpSource, board: str, url: str) -> None:
        self.http, self.board = http, board
        self.host, _, self.path = url.partition("/")
        self.key = f"canny:{url}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        seen_at = _undated(since)
        # Newest posts every run; on the first run also the top-voted ones.
        sorts = ["newest"] if seen_at > since else ["newest", "top"]
        for sort in sorts:
            response = await self.http.get(f"https://{self.host}/{self.path}", {"sort": sort})
            for item in parse_canny(response.text, self.board, self.host, seen_at):
                yield item


# --- the Source -----------------------------------------------------------------------------


class FeatureBoardsCollector(Collector):
    source_key = "feature_boards"

    def streams(self) -> list[Stream]:
        streams: list[Stream] = []
        for board in self.source.communities:
            platform, _, address = board.partition(":")
            if platform == "featurebase":
                streams.append(FeaturebaseStream(self.http, board, address.strip("/")))
            elif platform == "uservoice":
                streams.append(UserVoiceStream(self.http, board, address.strip("/")))
            elif platform == "canny":
                streams.append(CannyStream(self.http, board, address.strip("/")))
            else:
                raise ValueError(
                    f"feature board '{board}': start it with featurebase:, uservoice: or canny:"
                )
        return streams

    def community_name(self, external_ref: str) -> str:
        return external_ref.partition(":")[2]
