"""Shopify App Store reviews, from the public review pages (plain HTML, no auth).

One stream per Watch-listed app and star rating (1 to `max_review_rating`): the reviews page
filtered to that rating and sorted newest first, paged until reviews are older than the cursor.
Low ratings are where merchants describe what's missing or broken. The developer's reply under a
review isn't collected.
"""

import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from html.parser import HTMLParser

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream

BASE = "https://apps.shopify.com"
DEFAULT_MAX_RATING = 3
MAX_PAGES = 60
_REVIEW_START = re.compile(r'<div data-merchant-review="" data-review-content-id="(\d+)"')
_DATE = re.compile(
    r"(January|February|March|April|May|June|July|August|September|October|November|December)"
    r" \d{1,2}, \d{4}"
)


class _ReviewParser(HTMLParser):
    """Pulls the date, the review text and the store name out of one review block."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.date: str | None = None
        self.body: list[str] = []
        self.author: str | None = None
        self.in_body = False
        self.body_done = False
        self.in_heading = False

    def handle_starttag(self, tag, attrs):
        classes = dict(attrs).get("class") or ""
        if tag == "p" and "tw-break-words" in classes and not self.body_done:
            self.in_body = True
        if "tw-text-heading-xs" in classes and self.author is None:
            self.in_heading = True
        if tag == "br" and self.in_body:
            self.body.append("\n")

    def handle_endtag(self, tag):
        if tag == "p" and self.in_body:
            self.in_body, self.body_done = False, True
        if self.in_heading and tag in ("div", "span"):
            self.in_heading = self.author is None

    def handle_data(self, data):
        text = data.strip()
        if not text:
            return
        if self.date is None and _DATE.fullmatch(text):
            self.date = text
        elif self.in_body:
            self.body.append(data)
        elif self.in_heading and self.author is None and self.body_done:
            self.author = text
            self.in_heading = False


def parse_page(html: str, app: str, rating: int) -> list[RawItem]:
    starts = [(m.start(), m.group(1)) for m in _REVIEW_START.finditer(html)]
    items = []
    for i, (start, review_id) in enumerate(starts):
        end = starts[i + 1][0] if i + 1 < len(starts) else len(html)
        p = _ReviewParser()
        p.feed(html[start:end])
        body = "".join(p.body).strip()
        if not p.date or not body:
            continue
        created = datetime.strptime(p.date, "%B %d, %Y").replace(tzinfo=UTC)
        items.append(
            RawItem(
                external_id=f"{app}/{review_id}",
                community=app,
                kind="review",
                url=f"{BASE}/{app}/reviews?ratings%5B%5D={rating}&sort_by=newest#{review_id}",
                body=body,
                created_at=created,
                author=p.author,
                thread_external_id=f"{app}/{review_id}",
                extra={"rating": rating},
                raw={"review_id": review_id, "date": p.date},
            )
        )
    return items


class ReviewStream(Stream):
    def __init__(self, http: HttpSource, app: str, rating: int) -> None:
        self.http = http
        self.app = app
        self.rating = rating
        self.key = f"reviews:{app}:{rating}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        for page in range(1, MAX_PAGES + 1):
            response = await self.http.get(
                f"{BASE}/{self.app}/reviews",
                {"ratings[]": self.rating, "sort_by": "newest", "page": page},
            )
            items = parse_page(response.text, self.app, self.rating)
            for item in items:
                if item.created_at >= since:
                    yield item
            if not items or items[-1].created_at < since:
                return


class ShopifyAppsCollector(Collector):
    source_key = "shopify_apps"

    def streams(self) -> list[Stream]:
        max_rating = int(self.source.options.get("max_review_rating", DEFAULT_MAX_RATING))
        return [
            ReviewStream(self.http, app, rating)
            for app in self.source.communities
            for rating in range(1, max_rating + 1)
        ]
