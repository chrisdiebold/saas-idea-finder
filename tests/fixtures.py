"""Shared fixtures for tests needing Postgres with evidence, and a stand-in for LM Studio."""

import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx2
import numpy as np
import pytest
from sqlalchemy import create_engine, text

from ideafinder.config import load_config
from ideafinder.db import admin
from ideafinder.db.engine import get_sessionmaker
from ideafinder.db.models import (
    EMBEDDING_DIM,
    Community,
    Idea,
    IdeaSignal,
    Item,
    Signal,
    Source,
)
from ideafinder.llm import LLM

_rng = np.random.default_rng(7)


def direction(seed: int) -> np.ndarray:
    """A fixed random unit vector: one "topic"."""
    v = np.random.default_rng(seed).normal(size=EMBEDDING_DIM)
    return v / np.linalg.norm(v)


def near(base: np.ndarray, noise: float = 0.05) -> np.ndarray:
    """A unit vector close to `base`: another Signal about the same topic."""
    v = base + _rng.normal(scale=noise / np.sqrt(EMBEDDING_DIM), size=EMBEDDING_DIM)
    return v / np.linalg.norm(v)


@pytest.fixture
async def session(use_settings, test_database_url):
    settings = use_settings(test_database_url)
    admin.create_database_if_missing(settings)
    admin.upgrade(settings)
    engine = create_engine(settings.sqlalchemy_url)
    with engine.begin() as conn:
        conn.execute(
            text("TRUNCATE fit_profile, ideas, signals, items, communities, sources CASCADE")
        )
    engine.dispose()
    async with get_sessionmaker()() as s:
        yield s


DEFAULT_ANSWERS = {
    # Summaries are written for every published Idea; most tests don't care what they say.
    "summary": {
        "problem": "A problem.",
        "persona": "a persona",
        "quotes": [1],
        "payment_note": "No sign of paying.",
        "verdict": "Look closer.",
    },
}


class FakeModel:
    """Stands in for LM Studio. JSON answers are queued per task (a few tasks have a default);
    embeddings of known texts come back as given vectors (anything else a fixed random one)."""

    def __init__(self):
        self.answers: dict[str, list[dict]] = defaultdict(list)
        self.vectors: dict[str, np.ndarray] = {}
        self.prompts: dict[str, list[str]] = defaultdict(list)

    def handler(self, request):
        body = json.loads(request.content)
        if request.url.path.endswith("/embeddings"):
            data = [
                {"index": i, "embedding": self.vectors.get(t, direction(hash(t) % 1000)).tolist()}
                for i, t in enumerate(body["input"])
            ]
            return httpx2.Response(200, json={"data": data})
        task = body["response_format"]["json_schema"]["name"]
        self.prompts[task].append(body["messages"][1]["content"])
        if self.answers[task]:
            answer = json.dumps(self.answers[task].pop(0))
        elif task in DEFAULT_ANSWERS:
            answer = json.dumps(DEFAULT_ANSWERS[task])
        else:
            raise AssertionError(f"unexpected {task} call")
        return httpx2.Response(
            200, json={"choices": [{"message": {"content": answer}, "finish_reason": "stop"}]}
        )

    def answer(self, task: str, **data):
        self.answers[task].append(data)

    def name(self, title: str, problem: str, persona: str, problem_vector: np.ndarray):
        self.answer("name_idea", title=title, problem_statement=problem, persona=persona)
        self.vectors[f"clustering: {problem}"] = problem_vector


@pytest.fixture
def fake():
    return FakeModel()


@pytest.fixture
async def llm(repo_config_dir, fake):
    async with LLM(load_config(repo_config_dir).models, httpx2.MockTransport(fake.handler)) as m:
        yield m


class EvidenceBuilder:
    """Creates Sources, Communities, Items and embedded Signals in the test database."""

    def __init__(self, session):
        self.session = session
        self.n = 0
        self.source_id = None
        self.communities: dict[str, int] = {}

    async def _community(self, name: str) -> int:
        if self.source_id is None:
            source = Source(key="hackernews", name="HN", phase=1, enabled=True)
            self.session.add(source)
            await self.session.flush()
            self.source_id = source.id
        if name not in self.communities:
            community = Community(source_id=self.source_id, external_ref=name, name=name)
            self.session.add(community)
            await self.session.flush()
            self.communities[name] = community.id
        return self.communities[name]

    async def item(
        self,
        author: str | None,
        community: str = "c1",
        thread: str | None = None,
        created_at: datetime | None = None,
        prefilter: str = "keep",
        votes: int | None = None,
    ) -> Item:
        self.n += 1
        item = Item(
            source_id=None,
            community_id=await self._community(community),
            external_id=str(self.n),
            thread_external_id=thread,
            kind="comment",
            url=f"https://example.com/{self.n}",
            body="text",
            author=author,
            language="en",
            votes=votes,
            raw={},
            prefilter=prefilter,
            created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC) + timedelta(days=self.n),
        )
        item.source_id = self.source_id
        self.session.add(item)
        await self.session.flush()
        return item

    async def signal(
        self,
        vector: np.ndarray | None,
        author: str | None,
        kind: str = "pain",
        community: str = "c1",
        thread: str | None = None,
        created_at: datetime | None = None,
        intensity: int = 3,
    ) -> int:
        item = await self.item(author, community, thread, created_at)
        signal = Signal(
            item_id=item.id,
            kind=kind,
            problem_statement=f"problem {self.n}",
            persona="persona",
            intensity=intensity,
            quote="text",
            quote_verified=True,
            embedding=(vector if vector is not None else direction(self.n)).tolist(),
            model="test",
            prompt_version="test",
        )
        self.session.add(signal)
        await self.session.commit()
        return signal.id

    async def idea(self, signal_ids: list[int], state: str = "incubating", **fields) -> Idea:
        """An Idea holding the given Signals, created directly (no clustering)."""
        idea = Idea(
            title=fields.pop("title", "An idea"),
            problem_statement=fields.pop("problem_statement", "A problem"),
            persona=fields.pop("persona", "a persona"),
            state=state,
            **fields,
        )
        self.session.add(idea)
        await self.session.flush()
        for sid in signal_ids:
            self.session.add(IdeaSignal(signal_id=sid, idea_id=idea.id, similarity=0.9))
        await self.session.commit()
        return idea


@pytest.fixture
def evidence(session):
    return EvidenceBuilder(session)
