"""WordPress.org plugin reviews and support threads, via each plugin's RSS feeds. No auth.

Two streams per Watch-listed plugin: reviews (kept only at or below `max_review_rating` stars)
and support threads. Each feed holds only its latest 30 entries and can't be paged, so this
Source has no history beyond that; daily collection keeps up with even the busiest plugins.
"""

import re
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from datetime import datetime
from email.utils import parsedate_to_datetime

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream
from ideafinder.collectors.text import html_to_text

FEED = "https://wordpress.org/support/plugin/{slug}/{path}feed/"
DC_CREATOR = "{http://purl.org/dc/elements/1.1/}creator"
DEFAULT_MAX_RATING = 3

_TITLE_PREFIX = re.compile(r"^\[[^\]]*\]\s*")
_TITLE_RATING = re.compile(r"\s*\((\d) stars?\)\s*$")
_REPLIES = re.compile(r"^\s*Replies:\s*(\d+)\s*$", re.MULTILINE)
_RATING = re.compile(r"^\s*Rating:\s*(\d) stars?\s*$", re.MULTILINE)


def parse_entry(entry: ET.Element, slug: str, is_review: bool) -> RawItem:
    link = (entry.findtext("link") or "").strip()
    guid = (entry.findtext("guid") or link).strip()
    title = (entry.findtext("title") or "").strip()
    body = html_to_text(entry.findtext("description"))

    rating_match = _RATING.search(body) or _TITLE_RATING.search(title)
    replies_match = _REPLIES.search(body)
    body = _RATING.sub("", _REPLIES.sub("", body)).strip()
    title = _TITLE_RATING.sub("", _TITLE_PREFIX.sub("", title)).strip()

    extra: dict = {"replies": int(replies_match.group(1)) if replies_match else None}
    if is_review:
        extra["rating"] = int(rating_match.group(1)) if rating_match else None
    return RawItem(
        external_id=guid,
        community=slug,
        kind="review" if is_review else "post",
        url=link or guid,
        title=title or None,
        body=body,
        created_at=parsedate_to_datetime(entry.findtext("pubDate") or ""),
        author=(entry.findtext(DC_CREATOR) or "").strip() or None,
        thread_external_id=guid,
        extra=extra,
        raw={child.tag: child.text for child in entry},
    )


class FeedStream(Stream):
    def __init__(self, http: HttpSource, slug: str, is_review: bool, max_rating: int) -> None:
        self.http = http
        self.slug = slug
        self.is_review = is_review
        self.max_rating = max_rating
        self.key = f"{'reviews' if is_review else 'support'}:{slug}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        url = FEED.format(slug=self.slug, path="reviews/" if self.is_review else "")
        response = await self.http.get(url)
        channel = ET.fromstring(response.content).find("channel")
        for entry in channel.findall("item") if channel is not None else []:
            item = parse_entry(entry, self.slug, self.is_review)
            if item.created_at < since:
                continue
            rating = item.extra.get("rating")
            if self.is_review and (rating is None or rating > self.max_rating):
                continue
            yield item


class WordPressCollector(Collector):
    source_key = "wordpress"

    def streams(self) -> list[Stream]:
        max_rating = int(self.source.options.get("max_review_rating", DEFAULT_MAX_RATING))
        return [
            FeedStream(self.http, slug, is_review, max_rating)
            for slug in self.source.communities
            for is_review in (True, False)
        ]
