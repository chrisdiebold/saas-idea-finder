"""Hacker News via the Algolia search API (https://hn.algolia.com/api). No auth, full history.

Streams: one per pain-phrase query (stories and comments), plus all Show HN launches.
Algolia returns at most 1,000 hits per query, newest first, so long ranges are walked backwards
by narrowing the upper time bound.
"""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream
from ideafinder.collectors.text import html_to_text

API = "https://hn.algolia.com/api/v1/search_by_date"
ITEM_URL = "https://news.ycombinator.com/item?id={}"
COMMUNITY = "hackernews"
PAGE_SIZE = 100
MAX_REACHABLE = 1000  # Algolia stops paginating after this many hits


def parse_hit(hit: dict[str, Any]) -> RawItem:
    tags = hit.get("_tags") or []
    object_id = str(hit["objectID"])
    created = datetime.fromtimestamp(hit["created_at_i"], tz=UTC)
    if "comment" in tags:
        return RawItem(
            external_id=object_id,
            community=COMMUNITY,
            kind="comment",
            url=ITEM_URL.format(object_id),
            body=html_to_text(hit.get("comment_text")),
            created_at=created,
            author=hit.get("author"),
            thread_external_id=str(hit["story_id"]) if hit.get("story_id") else None,
            parent_external_id=str(hit["parent_id"]) if hit.get("parent_id") else None,
            extra={"story_title": hit.get("story_title")},
            raw=hit,
        )
    return RawItem(
        external_id=object_id,
        community=COMMUNITY,
        kind="post",
        url=ITEM_URL.format(object_id),
        title=hit.get("title"),
        body=html_to_text(hit.get("story_text")),
        created_at=created,
        author=hit.get("author"),
        votes=hit.get("points"),
        thread_external_id=object_id,
        extra={
            "link": hit.get("url"),
            "num_comments": hit.get("num_comments"),
            "show_hn": "show_hn" in tags,
            "ask_hn": "ask_hn" in tags,
        },
        raw=hit,
    )


class SearchStream(Stream):
    def __init__(self, http: HttpSource, key: str, params: dict[str, str]) -> None:
        self.http = http
        self.key = key
        self.params = params

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        since_ts = int(since.timestamp())
        upper: int | None = None  # inclusive upper bound once we have to walk backwards
        seen: set[str] = set()
        while True:
            window_filter = f"created_at_i>={since_ts}"
            if upper is not None:
                window_filter += f",created_at_i<={upper}"
            oldest: int | None = None
            new_in_window = 0
            page, total = 0, 0
            while True:
                data = await self.http.get_json(
                    API,
                    {
                        **self.params,
                        "numericFilters": window_filter,
                        "hitsPerPage": PAGE_SIZE,
                        "page": page,
                    },
                )
                total = data.get("nbHits", 0)
                for hit in data.get("hits", []):
                    oldest = (
                        hit["created_at_i"] if oldest is None else min(oldest, hit["created_at_i"])
                    )
                    if hit["objectID"] in seen:
                        continue
                    seen.add(hit["objectID"])
                    new_in_window += 1
                    yield parse_hit(hit)
                page += 1
                if page >= data.get("nbPages", 0):
                    break
            # Everything in the window was reachable, or nothing new came back: done.
            if total <= MAX_REACHABLE or oldest is None or new_in_window == 0:
                return
            upper = oldest  # inclusive, so same-second Items aren't lost; `seen` drops repeats


class HackerNewsCollector(Collector):
    source_key = "hackernews"

    def streams(self) -> list[Stream]:
        streams: list[Stream] = [
            SearchStream(self.http, f"query:{q}", {"query": f'"{q}"', "tags": "(story,comment)"})
            for q in self.queries.all()
        ]
        streams.append(SearchStream(self.http, "show_hn", {"tags": "show_hn"}))
        return streams

    def community_name(self, external_ref: str) -> str:
        return "Hacker News"
