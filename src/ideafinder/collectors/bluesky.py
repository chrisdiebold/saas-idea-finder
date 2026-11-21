"""Bluesky post search, for the pain-phrase queries.

Anonymous search returns only the first page (up to 100 newest posts per phrase); asking for the
next page is refused. That's enough for daily collection. With a free account and an app
password (IDEAFINDER_BLUESKY_HANDLE / IDEAFINDER_BLUESKY_APP_PASSWORD), search pages back to the
cursor, which allows a backfill.
"""

import re
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream

PUBLIC = "https://api.bsky.app/xrpc"
PDS = "https://bsky.social/xrpc"
PAGE_SIZE = 100
MAX_PAGES = 20
COMMUNITY = "bluesky"


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\u2019", "'")).lower()


def contains_phrase(text: str, phrase: str) -> bool:
    """Bluesky's search matches loosely even with quotes; keep posts that say the phrase."""
    return _normal(phrase) in _normal(text)


def parse_post(post: dict[str, Any], query: str) -> RawItem | None:
    record = post.get("record") or {}
    text = (record.get("text") or "").strip()
    if not text:
        return None
    handle = (post.get("author") or {}).get("handle")
    rkey = post["uri"].rsplit("/", 1)[-1]
    reply = record.get("reply") or {}
    root = (reply.get("root") or {}).get("uri")
    return RawItem(
        external_id=post["uri"],
        community=COMMUNITY,
        kind="comment" if reply else "post",
        url=f"https://bsky.app/profile/{handle}/post/{rkey}",
        body=text,
        created_at=datetime.fromisoformat(record["createdAt"].replace("Z", "+00:00")),
        author=handle,
        votes=post.get("likeCount"),
        thread_external_id=root or post["uri"],
        parent_external_id=(reply.get("parent") or {}).get("uri"),
        extra={"query": query, "langs": record.get("langs"), "replies": post.get("replyCount")},
        raw={"uri": post["uri"], "likeCount": post.get("likeCount")},
    )


class Session:
    """An optional logged-in session; without one, only the first page of results is read."""

    def __init__(self, http: HttpSource, handle: str | None, password: str | None) -> None:
        self.http, self.handle, self.password = http, handle, password
        self.token: str | None = None

    @property
    def available(self) -> bool:
        return bool(self.handle and self.password)

    async def headers(self) -> dict[str, str]:
        if not self.available:
            return {}
        if self.token is None:
            created = await self.http.post_json(
                f"{PDS}/com.atproto.server.createSession",
                {"identifier": self.handle, "password": self.password},
            )
            self.token = created["accessJwt"]
        return {"Authorization": f"Bearer {self.token}"}


class SearchStream(Stream):
    def __init__(self, http: HttpSource, query: str, session: Session) -> None:
        self.http, self.query, self.session = http, query, session
        self.key = f"search:{query}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        base = PDS if self.session.available else PUBLIC
        cursor = None
        for _ in range(MAX_PAGES if self.session.available else 1):
            params: dict[str, Any] = {
                "q": f'"{self.query}"',
                "sort": "latest",
                "since": since.isoformat(),
                "limit": PAGE_SIZE,
            }
            if cursor:
                params["cursor"] = cursor
            data = await self.http.get_json(
                f"{base}/app.bsky.feed.searchPosts", params, headers=await self.session.headers()
            )
            for post in data.get("posts", []):
                item = parse_post(post, self.query)
                if item and item.created_at >= since and contains_phrase(item.body, self.query):
                    yield item
            cursor = data.get("cursor")
            if not cursor or not data.get("posts"):
                return


class BlueskyCollector(Collector):
    source_key = "bluesky"

    def streams(self) -> list[Stream]:
        password = self.settings.bluesky_app_password
        session = Session(
            self.http,
            self.settings.bluesky_handle,
            password.get_secret_value() if password else None,
        )
        return [SearchStream(self.http, q, session) for q in self.queries.all()]

    def community_name(self, external_ref: str) -> str:
        return "Bluesky"
