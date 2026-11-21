"""The collector framework: what a collector produces, and polite HTTP access to Sources."""

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any, ClassVar, Literal

import httpx2
from aiolimiter import AsyncLimiter

from ideafinder.config import QueryConfig, SourceConfig
from ideafinder.settings import Settings

log = logging.getLogger(__name__)

ItemKind = Literal["post", "comment", "review", "issue", "question"]


@dataclass(frozen=True)
class RawItem:
    """One Item as collected, before storage. Text fields are plain text, not HTML."""

    external_id: str  # unique within the Source
    community: str  # the Community's external reference (e.g. a Jira project key)
    kind: ItemKind
    url: str
    body: str
    created_at: datetime  # timezone-aware
    title: str | None = None
    author: str | None = None
    votes: int | None = None
    thread_external_id: str | None = None
    parent_external_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)
    # The time the stream's cursor advances to. Usually created_at; Jira uses "last updated"
    # so that new comments and votes on old issues are picked up.
    cursor_time: datetime | None = None

    @property
    def cursor_value(self) -> datetime:
        return self.cursor_time or self.created_at


class Stream(ABC):
    """One independently-resumable sequence of Items, e.g. one search query or one Community."""

    key: str  # stable identifier, stored with the cursor

    @abstractmethod
    def fetch(self, since: datetime) -> AsyncIterator[RawItem]:
        """Yield Items created (or updated) at or after `since`, in any order."""


class SourceGone(Exception):
    """The Source refused us in a way retrying won't fix (quota exhausted, access revoked)."""


class HttpSource:
    """HTTP access to one Source: shared client, per-Source rate limit, retries with backoff."""

    RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

    def __init__(
        self,
        client: httpx2.AsyncClient,
        rate_limit_per_minute: int,
        max_attempts: int = 4,
        base_delay: float = 2.0,
    ) -> None:
        self.client = client
        self.limiter = AsyncLimiter(rate_limit_per_minute, 60)
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    async def get(self, url: str, params: dict[str, Any] | None = None) -> httpx2.Response:
        for attempt in range(1, self.max_attempts + 1):
            async with self.limiter:
                try:
                    response = await self.client.get(url, params=params)
                except httpx2.TransportError as exc:
                    if attempt == self.max_attempts:
                        raise
                    delay = self._backoff(attempt)
                    log.warning("%s: %s; retrying in %.0fs", url, exc, delay)
                    await asyncio.sleep(delay)
                    continue
            if response.status_code in self.RETRY_STATUSES and attempt < self.max_attempts:
                delay = self._retry_after(response) or self._backoff(attempt)
                log.warning("%s: HTTP %s; retrying in %.0fs", url, response.status_code, delay)
                await asyncio.sleep(delay)
                continue
            response.raise_for_status()
            return response
        raise AssertionError("unreachable")

    async def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return (await self.get(url, params)).json()

    def _backoff(self, attempt: int) -> float:
        return self.base_delay * 2 ** (attempt - 1) * (1 + random.random() / 4)

    @staticmethod
    def _retry_after(response: httpx2.Response) -> float | None:
        value = response.headers.get("Retry-After")
        if not value:
            return None
        if value.isdigit():
            return float(value)
        try:
            return max(
                0.0, (parsedate_to_datetime(value) - datetime.now().astimezone()).total_seconds()
            )
        except (TypeError, ValueError):
            return None


class Collector(ABC):
    """Fetches Items from one Source. Subclasses declare their streams."""

    source_key: ClassVar[str]

    def __init__(
        self, source: SourceConfig, queries: QueryConfig, http: HttpSource, settings: Settings
    ) -> None:
        self.source = source
        self.queries = queries
        self.http = http
        self.settings = settings

    @abstractmethod
    def streams(self) -> list[Stream]: ...

    def community_name(self, external_ref: str) -> str:
        """Human-readable name for a Community; collectors override when they know better."""
        return external_ref
