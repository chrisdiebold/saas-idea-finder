"""Product Hunt launches, via the official GraphQL API (free developer token).

Launches are Market evidence: products that exist. They aren't sent to Signal extraction
(the prefilter files them as `market`); they feed Competitors. The API is for non-commercial use
by default, which fits this personal tool. Only featured launches (the ones Product Hunt picks
for its homepage) are collected: all launches run to ~600 a day, mostly noise, and the API's
budget (6,250 complexity points per 15 minutes) covers about 1,200 launches.
Needs IDEAFINDER_PRODUCTHUNT_TOKEN (create one at
producthunt.com/v2/oauth/applications).
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, SourceGone, Stream

API = "https://api.producthunt.com/v2/api/graphql"
COMMUNITY = "producthunt"
PAGE_SIZE = 20
MAX_PAGES = 400  # 8,000 featured launches: a 12-month backfill is ~7,800

QUERY = """
query Launches($after: String, $postedAfter: DateTime, $first: Int!) {
  posts(order: NEWEST, featured: true, postedAfter: $postedAfter, after: $after, first: $first) {
    pageInfo { hasNextPage endCursor }
    edges { node {
      id name tagline description url website votesCount commentsCount createdAt
      topics(first: 5) { edges { node { name } } }
    } }
  }
}
"""


def parse_launch(node: dict[str, Any]) -> RawItem:
    topics = [e["node"]["name"] for e in (node.get("topics") or {}).get("edges", [])]
    return RawItem(
        external_id=node["id"],
        community=COMMUNITY,
        kind="post",
        url=node["url"],
        title=f"{node['name']}: {node.get('tagline') or ''}".strip(": "),
        body=(node.get("description") or node.get("tagline") or "").strip(),
        created_at=datetime.fromisoformat(node["createdAt"].replace("Z", "+00:00")),
        votes=node.get("votesCount"),
        thread_external_id=node["id"],
        extra={
            "launch": True,
            "product": node["name"],
            "website": node.get("website"),
            "topics": topics,
            "comments": node.get("commentsCount"),
        },
        raw=node,
    )


class LaunchStream(Stream):
    key = "launches"

    def __init__(self, http: HttpSource, token: str) -> None:
        self.http, self.token = http, token

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        after = None
        for _ in range(MAX_PAGES):
            body = await self.http.post_json(
                API,
                {
                    "query": QUERY,
                    "variables": {
                        "after": after,
                        "postedAfter": since.isoformat(),
                        "first": PAGE_SIZE,
                    },
                },
                headers={"Authorization": f"Bearer {self.token}"},
            )
            if body.get("errors"):
                raise SourceGone(f"Product Hunt refused the query: {body['errors'][0]}")
            posts = body["data"]["posts"]
            for edge in posts["edges"]:
                yield parse_launch(edge["node"])
            if not posts["pageInfo"]["hasNextPage"]:
                return
            after = posts["pageInfo"]["endCursor"]


class ProductHuntCollector(Collector):
    source_key = "producthunt"

    def streams(self) -> list[Stream]:
        token = self.settings.producthunt_token
        if token is None:
            raise SourceGone(
                "Product Hunt needs a free developer token: set IDEAFINDER_PRODUCTHUNT_TOKEN"
            )
        return [LaunchStream(self.http, token.get_secret_value())]

    def community_name(self, external_ref: str) -> str:
        return "Product Hunt"
