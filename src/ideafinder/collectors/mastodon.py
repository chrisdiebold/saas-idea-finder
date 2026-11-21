"""Mastodon hashtag timelines (no auth), one instance.

Full-text search only covers accounts that opted in, so hashtags are the way in: each
Watch-listed hashtag is a Community, followed backwards in time until the cursor. Boosts are
skipped (the original is collected under its own hashtag if it matches). Favourites count as
Votes.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream
from ideafinder.collectors.text import html_to_text

DEFAULT_INSTANCE = "mastodon.social"
PAGE_SIZE = 40
MAX_PAGES = 50


def parse_status(status: dict[str, Any], tag: str, instance: str) -> RawItem:
    account = status.get("account") or {}
    acct = account.get("acct") or account.get("username")
    return RawItem(
        external_id=status["uri"],
        community=tag,
        kind="post" if not status.get("in_reply_to_id") else "comment",
        url=status.get("url") or status["uri"],
        body=html_to_text(status.get("content") or ""),
        created_at=datetime.fromisoformat(status["created_at"].replace("Z", "+00:00")),
        author=f"{acct}@{instance}" if acct and "@" not in acct else acct,
        votes=status.get("favourites_count"),
        thread_external_id=status["uri"],
        extra={
            "language": status.get("language"),
            "replies": status.get("replies_count"),
            "boosts": status.get("reblogs_count"),
            "bot": bool(account.get("bot")),
        },
        raw={k: status.get(k) for k in ("id", "uri", "language", "favourites_count")},
    )


class HashtagStream(Stream):
    def __init__(self, http: HttpSource, instance: str, tag: str) -> None:
        self.http, self.instance, self.tag = http, instance, tag
        self.key = f"tag:{instance}:{tag}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        max_id = None
        for _ in range(MAX_PAGES):
            params: dict[str, Any] = {"limit": PAGE_SIZE}
            if max_id:
                params["max_id"] = max_id
            statuses = await self.http.get_json(
                f"https://{self.instance}/api/v1/timelines/tag/{self.tag}", params
            )
            if not statuses:
                return
            for status in statuses:
                if status.get("reblog") or (status.get("account") or {}).get("bot"):
                    continue
                item = parse_status(status, self.tag, self.instance)
                if item.created_at < since:
                    return  # the timeline is newest first
                yield item
            max_id = statuses[-1]["id"]


class MastodonCollector(Collector):
    source_key = "mastodon"

    def streams(self) -> list[Stream]:
        instance = self.source.options.get("instance", DEFAULT_INSTANCE)
        return [
            HashtagStream(self.http, instance, tag.lstrip("#")) for tag in self.source.communities
        ]

    def community_name(self, external_ref: str) -> str:
        return f"#{external_ref}"
