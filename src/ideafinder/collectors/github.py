"""GitHub issues on Watch-listed repositories, via the official REST API.

One stream per repository ("owner/name"): open issues updated since the cursor, newest first.
Reactions count as Votes. Pull requests are skipped. A general search across GitHub is mostly
noise (game mods, homework), so collection follows repositories you choose: open-source tools
whose users are the kind of people the Ideas are for.

Without a token GitHub allows 60 requests an hour, so each run stops early and resumes next time;
set IDEAFINDER_GITHUB_TOKEN (a fine-grained token with no permissions is enough) for 5,000.
"""

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx2

from ideafinder.collectors.base import Collector, HttpSource, RawItem, SourceGone, Stream

API = "https://api.github.com"
PAGE_SIZE = 100
MAX_PAGES_WITHOUT_TOKEN = 20


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def parse_issue(issue: dict[str, Any], repo: str) -> RawItem:
    reactions = issue.get("reactions") or {}
    return RawItem(
        external_id=f"{repo}#{issue['number']}",
        community=repo,
        kind="issue",
        url=issue["html_url"],
        title=issue.get("title"),
        body=(issue.get("body") or "").strip(),
        created_at=parse_time(issue["created_at"]),
        author=(issue.get("user") or {}).get("login"),
        votes=reactions.get("total_count"),
        thread_external_id=f"{repo}#{issue['number']}",
        extra={
            "labels": [label["name"] for label in issue.get("labels", [])],
            "comments": issue.get("comments"),
            "thumbs_up": reactions.get("+1"),
        },
        raw={k: issue.get(k) for k in ("number", "state", "labels", "reactions", "comments")},
        cursor_time=parse_time(issue["updated_at"]),
    )


class RepoStream(Stream):
    def __init__(self, http: HttpSource, repo: str, headers: dict[str, str], max_pages: int):
        self.http = http
        self.repo = repo
        self.headers = headers
        self.max_pages = max_pages
        self.key = f"issues:{repo}"

    async def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        for page in range(1, self.max_pages + 1):
            try:
                issues = await self.http.get_json(
                    f"{API}/repos/{self.repo}/issues",
                    {
                        "state": "open",
                        "sort": "updated",
                        "direction": "desc",
                        "since": since.isoformat(),
                        "per_page": PAGE_SIZE,
                        "page": page,
                    },
                    headers=self.headers,
                )
            except httpx2.HTTPStatusError as exc:
                if exc.response.status_code == 403:  # out of requests for this hour
                    raise SourceGone(
                        "GitHub's hourly limit was reached; the rest is collected next run. "
                        "Set IDEAFINDER_GITHUB_TOKEN to raise it."
                    ) from exc
                raise
            for issue in issues:
                if "pull_request" not in issue:
                    yield parse_issue(issue, self.repo)
            if len(issues) < PAGE_SIZE:
                return


class GitHubCollector(Collector):
    source_key = "github"

    def streams(self) -> list[Stream]:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        token = self.settings.github_token
        if token:
            headers["Authorization"] = f"Bearer {token.get_secret_value()}"
        max_pages = 1000 if token else MAX_PAGES_WITHOUT_TOKEN
        return [RepoStream(self.http, repo, headers, max_pages) for repo in self.source.communities]
