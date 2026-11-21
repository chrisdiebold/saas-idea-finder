"""Stack Exchange via its official API (https://api.stackexchange.com). Free key optional.

Per Watch-listed site: either every question (sites listed in `collect_all`, where every question
is a request) or one stream per pain-phrase query. Answers aren't collected: they're solutions,
not evidence of problems. The API's `backoff` instruction and daily quota are respected.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, SourceGone, Stream
from ideafinder.collectors.text import html_to_text, unescape

log = logging.getLogger(__name__)

API = "https://api.stackexchange.com/2.3"
PAGE_SIZE = 100
QUOTA_FLOOR = 5  # stop before the daily quota is completely gone


def parse_question(q: dict[str, Any], site: str) -> RawItem:
    external_id = f"{site}:{q['question_id']}"
    return RawItem(
        external_id=external_id,
        community=site,
        kind="question",
        url=q["link"],
        title=unescape(q.get("title")),
        body=html_to_text(q.get("body")),
        created_at=datetime.fromtimestamp(q["creation_date"], tz=UTC),
        author=unescape((q.get("owner") or {}).get("display_name")),
        votes=q.get("score"),
        thread_external_id=external_id,
        extra={
            "view_count": q.get("view_count"),
            "answer_count": q.get("answer_count"),
            "is_answered": q.get("is_answered"),
            "tags": q.get("tags", []),
        },
        raw=q,
    )


class QuestionStream(Stream):
    def __init__(
        self,
        http: HttpSource,
        site: str,
        key: str,
        path: str,
        params: dict[str, Any],
        api_key: str | None,
    ) -> None:
        self.http = http
        self.site = site
        self.key = key
        self.path = path
        self.params = params
        self.api_key = api_key

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        page = 1
        while True:
            params = {
                **self.params,
                "site": self.site,
                "fromdate": int(since.timestamp()),
                "sort": "creation",
                "order": "asc",
                "filter": "withbody",
                "pagesize": PAGE_SIZE,
                "page": page,
            }
            if self.api_key:
                params["key"] = self.api_key
            data = await self.http.get_json(f"{API}/{self.path}", params)
            for question in data.get("items", []):
                yield parse_question(question, self.site)

            if data.get("quota_remaining", QUOTA_FLOOR + 1) <= QUOTA_FLOOR:
                raise SourceGone(
                    f"Stack Exchange daily quota nearly used up ({data.get('quota_remaining')} "
                    "left); the rest will be collected tomorrow"
                )
            if data.get("backoff"):
                log.info("Stack Exchange asked us to wait %ss", data["backoff"])
                await asyncio.sleep(data["backoff"])
            if not data.get("has_more"):
                return
            page += 1


class StackExchangeCollector(Collector):
    source_key = "stackexchange"

    def streams(self) -> list[Stream]:
        key = self.settings.stackexchange_key
        api_key = key.get_secret_value() if key else None
        collect_all = set(self.source.options.get("collect_all", []))
        streams: list[Stream] = []
        for site in self.source.communities:
            if site in collect_all:
                streams.append(
                    QuestionStream(self.http, site, f"{site}:all", "questions", {}, api_key)
                )
                continue
            for q in self.queries.all():
                streams.append(
                    QuestionStream(
                        self.http,
                        site,
                        f"{site}:query:{q}",
                        "search/advanced",
                        {"q": f'"{q}"'},
                        api_key,
                    )
                )
        return streams
