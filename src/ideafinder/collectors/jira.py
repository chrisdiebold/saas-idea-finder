"""Atlassian's public Jira (https://jira.atlassian.com) via its REST API. No auth.

One stream per Watch-listed project: unresolved Suggestions updated since the cursor, each issue
plus its comments. The cursor follows "updated", so new comments and votes on old issues are seen.
"""

from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

from ideafinder.collectors.base import Collector, HttpSource, RawItem, Stream

BASE = "https://jira.atlassian.com"
SEARCH = f"{BASE}/rest/api/2/search"
FIELDS = "summary,description,votes,created,updated,reporter,comment,status"
PAGE_SIZE = 100
PROJECT_NAMES = {
    "JRACLOUD": "Jira Platform Cloud",
    "CONFCLOUD": "Confluence Cloud",
    "JSWCLOUD": "Jira Cloud (Software)",
}


def parse_time(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%f%z")


def _name(person: dict[str, Any] | None) -> str | None:
    return (person or {}).get("displayName")


def parse_issue(issue: dict[str, Any], project: str) -> list[RawItem]:
    """The issue as one Item, followed by one Item per comment."""
    key = issue["key"]
    fields = issue["fields"]
    updated = parse_time(fields["updated"])
    items = [
        RawItem(
            external_id=key,
            community=project,
            kind="issue",
            url=f"{BASE}/browse/{key}",
            title=fields.get("summary"),
            body=(fields.get("description") or "").strip(),
            created_at=parse_time(fields["created"]),
            author=_name(fields.get("reporter")),
            votes=(fields.get("votes") or {}).get("votes"),
            thread_external_id=key,
            extra={"status": (fields.get("status") or {}).get("name")},
            raw=issue,
            cursor_time=updated,
        )
    ]
    for comment in (fields.get("comment") or {}).get("comments", []):
        items.append(
            RawItem(
                external_id=f"{key}/comment/{comment['id']}",
                community=project,
                kind="comment",
                url=f"{BASE}/browse/{key}?focusedCommentId={comment['id']}",
                body=(comment.get("body") or "").strip(),
                created_at=parse_time(comment["created"]),
                author=_name(comment.get("author")),
                thread_external_id=key,
                parent_external_id=key,
                raw=comment,
                cursor_time=updated,
            )
        )
    return items


class ProjectStream(Stream):
    def __init__(self, http: HttpSource, project: str) -> None:
        self.http = http
        self.project = project
        self.key = f"project:{project}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        # JQL compares in the server's timezone; a day of overlap makes that irrelevant.
        since_text = (since - timedelta(days=1)).strftime("%Y/%m/%d %H:%M")
        jql = (
            f'project = "{self.project}" AND type = Suggestion AND resolution = Unresolved '
            f'AND updated >= "{since_text}" ORDER BY updated ASC'
        )
        start = 0
        while True:
            data = await self.http.get_json(
                SEARCH,
                {"jql": jql, "fields": FIELDS, "maxResults": PAGE_SIZE, "startAt": start},
            )
            issues = data.get("issues", [])
            for issue in issues:
                for item in parse_issue(issue, self.project):
                    yield item
            start += len(issues)
            if not issues or start >= data.get("total", 0):
                return


class JiraCollector(Collector):
    source_key = "jira_atlassian"

    def streams(self) -> list[Stream]:
        return [ProjectStream(self.http, p) for p in self.source.communities]

    def community_name(self, external_ref: str) -> str:
        return PROJECT_NAMES.get(external_ref, external_ref)
