"""Clustering: group Signals into Ideas (PLAN.md §3 step 5, ADR 0001).

Clustering is incremental so Idea ids stay stable:
- A newly embedded Signal joins the nearest Idea if it's close enough (archived Ideas included;
  they keep collecting evidence). Otherwise it waits in the holding pool.
- Every so often HDBSCAN runs over the pool. Tight groups with more than one Author become new
  Incubating Ideas, and the local model names them.
- Ideas with the same problem but different Personas are linked as Related.

Signal embeddings cover problem + Persona, so the same pain in two industries stays apart.
Market Signals never join Ideas directly; they reach Ideas through Competitors.
"""

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import Text, cast, delete, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ClusteringRules
from ideafinder.db.models import Idea, IdeaRelation, IdeaSignal, Item, Signal, Source
from ideafinder.llm import LLM, ModelUnavailable, ServerUnreachable
from ideafinder.pipeline.embed import PREFIX

log = logging.getLogger(__name__)

CLUSTERED_KINDS = ("pain", "request", "complaint", "payment")
STATES = ("incubating", "inbox", "working", "archived")
# For Merge: the target takes the more advanced state of the two. Archived ranks below the
# Inbox because new evidence arriving through a Merge is a reason to look again.
STATE_RANK = {"incubating": 0, "archived": 1, "inbox": 2, "working": 3}
MAX_POOL = 15_000  # HDBSCAN memory grows with the square of this; take the newest Signals
NAMING_SAMPLE = 20  # Signals closest to the centre that the model sees when naming an Idea


class ClusterError(Exception):
    """A Merge or Split that can't be done as asked."""


# --- attaching to existing Ideas ------------------------------------------------------------


@dataclass
class AttachReport:
    attached: int = 0
    pooled: int = 0
    ideas_touched: set[int] = field(default_factory=set)


def _unassigned():
    """Signals that could belong to an Idea but don't yet: the holding pool and new arrivals."""
    return (
        select(Signal)
        .outerjoin(IdeaSignal, IdeaSignal.signal_id == Signal.id)
        .where(
            IdeaSignal.signal_id.is_(None),
            Signal.embedding.is_not(None),
            Signal.kind.in_(CLUSTERED_KINDS),
        )
    )


def _live_ideas():
    return select(Idea).where(Idea.merged_into_id.is_(None), Idea.centroid.is_not(None))


async def attach_pending(session: AsyncSession, rules: ClusteringRules) -> AttachReport:
    """Put each unassigned Signal into its nearest Idea if it's at least `attach_similarity`."""
    report = AttachReport()
    if not await session.scalar(select(func.count()).select_from(_live_ideas().subquery())):
        report.pooled = await session.scalar(
            select(func.count()).select_from(_unassigned().subquery())
        )
        return report
    signals = (await session.scalars(_unassigned().order_by(Signal.id))).all()
    for signal in signals:
        distance = Idea.centroid.cosine_distance(signal.embedding)
        nearest = (
            await session.execute(
                _live_ideas().with_only_columns(Idea.id, distance).order_by(distance).limit(1)
            )
        ).first()
        similarity = 1 - nearest[1] if nearest else 0.0
        if nearest and similarity >= rules.attach_similarity:
            session.add(IdeaSignal(signal_id=signal.id, idea_id=nearest[0], similarity=similarity))
            report.attached += 1
            report.ideas_touched.add(nearest[0])
        else:
            report.pooled += 1
    # Centroids move after the batch rather than per Signal, so the order Signals arrive in
    # within one batch doesn't matter.
    await refresh_ideas(session, report.ideas_touched)
    await session.commit()
    return report


async def refresh_ideas(session: AsyncSession, idea_ids: set[int] | Sequence[int]) -> None:
    """Recompute centroid and first/last seen from the Ideas' current Signals."""
    if not idea_ids:
        return
    await session.execute(
        text(
            """
            UPDATE ideas SET centroid = sub.c, first_seen = sub.f, last_seen = sub.l
            FROM (
                SELECT x.idea_id, avg(s.embedding) AS c,
                       min(i.created_at) AS f, max(i.created_at) AS l
                FROM idea_signals x
                JOIN signals s ON s.id = x.signal_id
                JOIN items i ON i.id = s.item_id
                WHERE x.idea_id = ANY(:ids)
                GROUP BY x.idea_id
            ) sub
            WHERE ideas.id = sub.idea_id
            """
        ),
        {"ids": list(idea_ids)},
    )


# --- forming new Ideas from the pool --------------------------------------------------------


@dataclass(frozen=True)
class PoolSignal:
    id: int
    author: str  # Item author, or a per-Item stand-in when the Source has none
    problem_statement: str
    persona: str
    kind: str
    vector: np.ndarray  # unit length


@dataclass(frozen=True)
class Group:
    """A candidate Idea: pooled Signals that belong together, with similarities to its centre."""

    members: list[PoolSignal]
    similarities: list[float]

    @property
    def authors(self) -> int:
        return len({m.author for m in self.members})


def _unit(rows: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(rows, axis=1, keepdims=True)
    return rows / np.where(norms == 0, 1, norms)


def find_groups(pool: Sequence[PoolSignal], rules: ClusteringRules) -> list[Group]:
    """Run HDBSCAN on the pool and keep the groups that are tight and have enough Authors.

    On unit vectors, Euclidean distance orders points the same way as cosine distance. HDBSCAN
    clusters can have loose edges, so members less similar to the group's centre than the
    attach threshold go back to the pool: an Idea should only hold Signals that would have
    attached to it.

    HDBSCAN won't return a single cluster covering the whole pool (allow_single_cluster=False):
    on a large, mixed pool that "cluster" is meaningless. The cost is that a tiny pool holding
    just one group forms nothing until other evidence arrives.
    """
    if len(pool) < rules.min_cluster_size:
        return []
    from sklearn.cluster import HDBSCAN

    vectors = np.stack([p.vector for p in pool])
    labels = HDBSCAN(min_cluster_size=rules.min_cluster_size, copy=True).fit_predict(vectors)
    groups = []
    for label in sorted(set(labels) - {-1}):
        idx = np.flatnonzero(labels == label)
        centre = _unit(vectors[idx].mean(axis=0, keepdims=True))[0]
        sims = vectors[idx] @ centre
        keep = [
            (pool[i], float(s))
            for i, s in zip(idx, sims, strict=True)
            if s >= rules.attach_similarity
        ]
        group = Group([m for m, _ in keep], [s for _, s in keep])
        if len(group.members) >= rules.min_cluster_size and group.authors >= rules.min_authors:
            groups.append(group)
    return groups


async def load_pool(session: AsyncSession) -> list[PoolSignal]:
    rows = (
        await session.execute(
            _unassigned()
            .join(Item, Item.id == Signal.item_id)
            .with_only_columns(Signal, Item.author)
            .order_by(Signal.id.desc())
            .limit(MAX_POOL)
        )
    ).all()
    if not rows:
        return []
    vectors = _unit(np.array([s.embedding for s, _ in rows], dtype=np.float32))
    return [
        PoolSignal(
            id=s.id,
            author=author or f"item:{s.item_id}",
            problem_statement=s.problem_statement,
            persona=s.persona,
            kind=s.kind,
            vector=v,
        )
        for (s, author), v in zip(rows, vectors, strict=True)
    ]


NAMING_SYSTEM = """\
You name groups of evidence about software problems. Each group should be ONE recurring problem
experienced by ONE kind of person (the Persona), defined by industry or role.

Given problem statements taken from different people's posts, write:
- title: at most 8 words, plain words, naming the problem and who has it
  (e.g. "Landscapers re-texting crews after weather delays"). Not a product name.
- problem_statement: one sentence describing the problem itself, not a solution.
- persona: the kind of person who has it, as specific as the evidence allows
  (e.g. "owner of a small landscaping company", "Jira administrator at a large company").

If the statements disagree, describe what most of them share. Don't invent details."""

NAMING_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "problem_statement": {"type": "string"},
        "persona": {"type": "string"},
    },
    "required": ["title", "problem_statement", "persona"],
    "additionalProperties": False,
}


def render_for_naming(members: Sequence[PoolSignal], similarities: Sequence[float]) -> str:
    ranked = sorted(zip(similarities, members, strict=True), key=lambda p: -p[0])
    lines = [f"{len(members)} Signals from {len({m.author for m in members})} different people:"]
    for _, m in ranked[:NAMING_SAMPLE]:
        lines.append(f"- [{m.kind}] {m.problem_statement} (Persona: {m.persona})")
    return "\n".join(lines)


@dataclass(frozen=True)
class IdeaName:
    title: str
    problem_statement: str
    persona: str


async def name_idea(
    llm: LLM, members: Sequence[PoolSignal], similarities: Sequence[float]
) -> IdeaName:
    result = await llm.json(
        "name_idea",
        NAMING_SYSTEM,
        render_for_naming(members, similarities),
        NAMING_SCHEMA,
        max_tokens=400,
    )
    data = {k: (result.data.get(k) or "").strip() for k in NAMING_SCHEMA["required"]}
    if not all(data.values()):
        raise ModelUnavailable(f"{result.model} left part of the Idea's name empty: {data}")
    return IdeaName(**data)


async def problem_embedding(llm: LLM, problem_statement: str) -> list[float]:
    return (await llm.embed([f"{PREFIX}{problem_statement}"]))[0]


async def create_idea(
    session: AsyncSession,
    llm: LLM,
    members: Sequence[PoolSignal],
    similarities: Sequence[float | None],
    rules: ClusteringRules,
) -> Idea:
    """Name a group of Signals and store it as a new Incubating Idea (not committed)."""
    name = await name_idea(llm, members, [s or 0.0 for s in similarities])
    idea = Idea(
        title=name.title,
        problem_statement=name.problem_statement,
        persona=name.persona,
        state="incubating",
        problem_embedding=await problem_embedding(llm, name.problem_statement),
    )
    session.add(idea)
    await session.flush()
    for member, similarity in zip(members, similarities, strict=True):
        await session.merge(IdeaSignal(signal_id=member.id, idea_id=idea.id, similarity=similarity))
    await session.flush()
    await refresh_ideas(session, [idea.id])
    await link_related(session, idea.id, rules)
    return idea


@dataclass
class FormReport:
    pool: int = 0
    groups: int = 0
    created: list[int] = field(default_factory=list)
    failed: int = 0


async def form_ideas(session: AsyncSession, llm: LLM, rules: ClusteringRules) -> FormReport:
    """Cluster the holding pool and turn qualifying groups into Incubating Ideas."""
    pool = await load_pool(session)
    report = FormReport(pool=len(pool))
    groups = await asyncio.to_thread(find_groups, pool, rules)
    report.groups = len(groups)
    for group in groups:
        try:
            idea = await create_idea(session, llm, group.members, group.similarities, rules)
        except ServerUnreachable:
            raise
        except ModelUnavailable as exc:
            # Leave the group in the pool; the next run will try again.
            await session.rollback()
            report.failed += 1
            log.warning("couldn't name a group of %d Signals: %s", len(group.members), exc)
            continue
        await session.commit()
        report.created.append(idea.id)
    return report


# --- Related --------------------------------------------------------------------------------


async def link_related(session: AsyncSession, idea_id: int, rules: ClusteringRules) -> int:
    """Link an Idea to others with the same problem (by problem embedding, Persona left out)."""
    idea = await session.get(Idea, idea_id)
    if idea is None or idea.problem_embedding is None:
        return 0
    await session.execute(
        delete(IdeaRelation).where(
            or_(IdeaRelation.idea_id == idea_id, IdeaRelation.related_idea_id == idea_id)
        )
    )
    distance = Idea.problem_embedding.cosine_distance(idea.problem_embedding)
    candidates = (
        await session.execute(
            select(Idea.id, distance).where(
                Idea.id != idea_id,
                Idea.merged_into_id.is_(None),
                Idea.problem_embedding.is_not(None),
                distance <= 1 - rules.related_similarity,
            )
        )
    ).all()
    for other_id, d in candidates:
        low, high = sorted((idea_id, other_id))
        await session.merge(IdeaRelation(idea_id=low, related_idea_id=high, similarity=1 - d))
    await session.flush()
    return len(candidates)


# --- Merge and Split ------------------------------------------------------------------------


async def _live(session: AsyncSession, idea_id: int) -> Idea:
    idea = await session.get(Idea, idea_id)
    if idea is None:
        raise ClusterError(f"there's no Idea {idea_id}")
    if idea.merged_into_id is not None:
        raise ClusterError(f"Idea {idea_id} was merged into Idea {idea.merged_into_id}")
    return idea


async def merge(
    session: AsyncSession, source_id: int, target_id: int, rules: ClusteringRules
) -> Idea:
    """Merge `source` into `target`. The target keeps its identity and the more advanced state."""
    if source_id == target_id:
        raise ClusterError("can't merge an Idea into itself")
    source = await _live(session, source_id)
    target = await _live(session, target_id)
    await session.execute(
        update(IdeaSignal).where(IdeaSignal.idea_id == source_id).values(idea_id=target_id)
    )
    # Anything merged into the source earlier now points straight at the target.
    await session.execute(
        update(Idea).where(Idea.merged_into_id == source_id).values(merged_into_id=target_id)
    )
    await session.execute(
        delete(IdeaRelation).where(
            or_(IdeaRelation.idea_id == source_id, IdeaRelation.related_idea_id == source_id)
        )
    )
    if STATE_RANK[source.state] > STATE_RANK[target.state]:
        target.state = source.state
    source.merged_into_id = target_id
    await session.flush()
    await refresh_ideas(session, [target_id])
    await link_related(session, target_id, rules)
    await session.commit()
    return target


async def split(
    session: AsyncSession,
    llm: LLM,
    idea_id: int,
    signal_ids: Sequence[int],
    rules: ClusteringRules,
) -> Idea:
    """Move some of an Idea's Signals into a new Idea. The original keeps its identity and state;
    the split-off part starts as Incubating and follows the normal rules."""
    await _live(session, idea_id)
    wanted = set(signal_ids)
    rows = (
        await session.execute(
            select(Signal, Item.author)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .join(Item, Item.id == Signal.item_id)
            .where(IdeaSignal.idea_id == idea_id)
        )
    ).all()
    owned = {s.id for s, _ in rows}
    if missing := wanted - owned:
        raise ClusterError(f"Signals {sorted(missing)} aren't in Idea {idea_id}")
    if not wanted:
        raise ClusterError("name at least one Signal to split off")
    if wanted == owned:
        raise ClusterError("that's every Signal in the Idea; split off only some of them")
    chosen = [(s, a) for s, a in rows if s.id in wanted]
    vectors = _unit(np.array([s.embedding for s, _ in chosen], dtype=np.float32))
    members = [
        PoolSignal(s.id, a or f"item:{s.item_id}", s.problem_statement, s.persona, s.kind, v)
        for (s, a), v in zip(chosen, vectors, strict=True)
    ]
    new = await create_idea(session, llm, members, [None] * len(members), rules)
    await refresh_ideas(session, [idea_id])
    await session.commit()
    return new


# --- reading --------------------------------------------------------------------------------


@dataclass(frozen=True)
class IdeaRow:
    idea: Idea
    signals: int
    authors: int
    communities: int


async def list_ideas(
    session: AsyncSession, state: str | None = None, limit: int = 50
) -> list[IdeaRow]:
    """Live (not merged) Ideas with evidence counts, most Authors first."""
    # Items without an Author (some Sources don't give one) each count as their own Author.
    authors = func.count(func.distinct(func.coalesce(Item.author, "item:" + cast(Item.id, Text))))
    query = (
        select(
            Idea,
            func.count(IdeaSignal.signal_id),
            authors,
            func.count(func.distinct(Item.community_id)),
        )
        .join(IdeaSignal, IdeaSignal.idea_id == Idea.id)
        .join(Signal, Signal.id == IdeaSignal.signal_id)
        .join(Item, Item.id == Signal.item_id)
        .where(Idea.merged_into_id.is_(None))
        .group_by(Idea.id)
        .order_by(authors.desc(), Idea.last_seen.desc())
        .limit(limit)
    )
    if state:
        query = query.where(Idea.state == state)
    return [IdeaRow(*row) for row in (await session.execute(query)).all()]


@dataclass(frozen=True)
class SignalRow:
    signal: Signal
    similarity: float | None
    author: str | None
    source: str


@dataclass(frozen=True)
class IdeaDetail:
    idea: Idea
    related: list[tuple[Idea, float]]
    signals: list[SignalRow]


async def idea_detail(session: AsyncSession, idea_id: int) -> IdeaDetail | None:

    idea = await session.get(Idea, idea_id)
    if idea is None:
        return None
    other = func.coalesce(func.nullif(IdeaRelation.idea_id, idea_id), IdeaRelation.related_idea_id)
    related = (
        await session.execute(
            select(Idea, IdeaRelation.similarity)
            .join(IdeaRelation, Idea.id == other)
            .where(or_(IdeaRelation.idea_id == idea_id, IdeaRelation.related_idea_id == idea_id))
            .order_by(IdeaRelation.similarity.desc())
        )
    ).all()
    signals = (
        await session.execute(
            select(Signal, IdeaSignal.similarity, Item.author, Source.key)
            .join(IdeaSignal, IdeaSignal.signal_id == Signal.id)
            .join(Item, Item.id == Signal.item_id)
            .join(Source, Source.id == Item.source_id)
            .where(IdeaSignal.idea_id == idea_id)
            .order_by(IdeaSignal.similarity.desc().nulls_last(), Signal.id)
        )
    ).all()
    return IdeaDetail(
        idea=idea,
        related=[(i, s or 0.0) for i, s in related],
        signals=[SignalRow(*row) for row in signals],
    )
