"""Zapier Community topics, via its RSS feed (no auth).

Each entry carries an excerpt of the topic, not the whole post. The feed holds only the latest
100 topics and can't be paged, so this Source has no history: it only keeps up if collection
runs often enough (a busy day can pass 100). That's why adding it is
the trigger for moving collection to an always-on machine (ADR 0003). Topics are mostly people
asking how to connect two apps, which is where missing integrations show up.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from datetime import datetime
from email.utils import parsedate_to_datetime

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream
from ideafinder.collectors.text import html_to_text

FEED = "https://community.zapier.com/feed/topics"
COMMUNITY = "zapier"
# The feed drops line breaks, gluing sentences ("everyone!I'm"). Re-split only after sentence
# punctuation, since splitting every lower-to-upper join would break names like "WooCommerce".
_GLUED = re.compile(r"([.!?])([A-Z])")


def unglue(text: str) -> str:
    return _GLUED.sub(r"\1 \2", text)


def parse_entry(entry: ET.Element) -> RawItem:
    link = (entry.findtext("link") or "").strip()
    return RawItem(
        external_id=link.rsplit("-", 1)[-1] if link.rsplit("-", 1)[-1].isdigit() else link,
        community=COMMUNITY,
        kind="post",
        url=link,
        title=(entry.findtext("title") or "").strip() or None,
        body=unglue(html_to_text(entry.findtext("description"))),
        created_at=parsedate_to_datetime(entry.findtext("pubDate") or ""),
        thread_external_id=link,
        extra={"category": (entry.findtext("category") or "").strip() or None},
        raw={child.tag: child.text for child in entry},
    )


class TopicFeed(Stream):
    key = "topics"

    def __init__(self, http: HttpSource) -> None:
        self.http = http

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        response = await self.http.get(FEED)
        channel = ET.fromstring(response.content).find("channel")
        for entry in channel.findall("item") if channel is not None else []:
            item = parse_entry(entry)
            if item.created_at >= since:
                yield item


class ZapierCollector(Collector):
    source_key = "zapier"

    def streams(self) -> list[Stream]:
        return [TopicFeed(self.http)]

    def community_name(self, external_ref: str) -> str:
        return "Zapier Community"
