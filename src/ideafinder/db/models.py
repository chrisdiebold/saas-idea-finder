"""SQLAlchemy models. Tables are added milestone by milestone (see PLAN.md §6)."""

from datetime import date, datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


class WorkerHeartbeat(Base):
    """Last time a worker processed a job on each queue. Shown on the status page."""

    __tablename__ = "worker_heartbeats"

    queue: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Source(Base):
    """A website or service evidence is collected from. Mirrors config/sources.yaml."""

    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    phase: Mapped[int] = mapped_column(SmallInteger)
    enabled: Mapped[bool]


class Community(Base):
    """A place within a Source where a group of people gathers."""

    __tablename__ = "communities"
    __table_args__ = (UniqueConstraint("source_id", "external_ref"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_ref: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(200))
    # watched (on the Watch list) / suggested / ignored
    watch_status: Mapped[str] = mapped_column(String(16), server_default="watched")
    suggestion_evidence: Mapped[dict[str, Any] | None]


class Item(Base):
    """A single piece of content collected from a Source, written by one Author."""

    __tablename__ = "items"
    __table_args__ = (UniqueConstraint("source_id", "external_id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    community_id: Mapped[int] = mapped_column(ForeignKey("communities.id"), index=True)
    external_id: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))  # post / comment / review / issue / question
    thread_external_id: Mapped[str | None] = mapped_column(String(300), index=True)
    parent_external_id: Mapped[str | None] = mapped_column(String(300))
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(300))
    language: Mapped[str] = mapped_column(String(8))
    votes: Mapped[int | None]
    extra: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw: Mapped[dict[str, Any]]
    # Prefilter outcome (M2): keep (send to extraction) / drop / vote (counts as a Vote on the
    # Thread's root Item) / market (a launch; used for Competitors, not extraction). NULL = not yet.
    prefilter: Mapped[str | None] = mapped_column(String(8), index=True)
    prefilter_reason: Mapped[str | None] = mapped_column(String(32))
    extracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


EMBEDDING_DIM = 768  # nomic-embed-text-v1.5, served by LM Studio


class Signal(Base):
    """One piece of evidence taken from an Item (see GLOSSARY.md)."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # pain / request / complaint / payment / market
    problem_statement: Mapped[str] = mapped_column(Text)
    persona: Mapped[str] = mapped_column(Text)
    intensity: Mapped[int] = mapped_column(SmallInteger)
    quote: Mapped[str] = mapped_column(Text)
    quote_verified: Mapped[bool] = mapped_column(Boolean)  # the quote really appears in the Item
    workaround: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    model: Mapped[str] = mapped_column(String(200))
    prompt_version: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Competitor(Base):
    """An existing product; one per product, shared across Ideas."""

    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    url: Mapped[str | None] = mapped_column(Text)
    pricing: Mapped[str | None] = mapped_column(Text)
    created_from: Mapped[str] = mapped_column(String(16))  # mention / briefing / manual
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CompetitorAlias(Base):
    """A normalised name or domain that identifies a Competitor ("jobber", "getjobber")."""

    __tablename__ = "competitor_aliases"

    alias: Mapped[str] = mapped_column(String(200), primary_key=True)
    competitor_id: Mapped[int] = mapped_column(
        ForeignKey("competitors.id", ondelete="CASCADE"), index=True
    )


class SignalCompetitor(Base):
    """A product named in a Signal (Complaint Signals are about it; others just mention it)."""

    __tablename__ = "signal_competitors"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    competitor_id: Mapped[int] = mapped_column(
        ForeignKey("competitors.id", ondelete="CASCADE"), primary_key=True
    )


class Idea(Base):
    """A recurring problem experienced by one Persona (ADR 0001). Ids stay stable over time."""

    __tablename__ = "ideas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    problem_statement: Mapped[str] = mapped_column(Text)
    persona: Mapped[str] = mapped_column(Text)
    # incubating / inbox / working / archived (PLAN.md §7)
    state: Mapped[str] = mapped_column(String(16), server_default="incubating", index=True)
    seeded: Mapped[bool] = mapped_column(Boolean, server_default="false")
    merged_into_id: Mapped[int | None] = mapped_column(ForeignKey("ideas.id"), index=True)
    # Mean of the member Signals' embeddings (problem + Persona); new Signals attach by it.
    centroid: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    # The problem statement alone, so the same problem for another Persona is found as Related.
    problem_embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    first_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Ranking (M4)
    score: Mapped[float | None] = mapped_column(index=True)  # 0-100
    score_breakdown: Mapped[dict[str, Any] | None]  # components, weights and the raw counts
    scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Fit check result: {"deal_breakers": [...], "fit": 0-1, "b2b": bool, "solo_buildable": 0-1,
    # "notes": str}, and the Fit profile version it was checked against.
    fit: Mapped[dict[str, Any] | None]
    fit_version: Mapped[int | None]
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    archive_reason: Mapped[str | None] = mapped_column(String(32))  # current one, if archived
    resurfaced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Summary (M5): {"problem", "persona", "quotes": [{"text", "author", "source", "url"}],
    # "payment_note", "verdict"}, rewritten when the evidence changes significantly.
    summary: Mapped[dict[str, Any] | None]
    summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_signal_count: Mapped[int | None]
    summary_community_ids: Mapped[list[Any] | None]
    notes_md: Mapped[str | None] = mapped_column(Text)  # yours
    # US industry codes (NAICS) for the Persona, for market math. None = not mapped yet;
    # [] = mapped, but the Persona isn't tied to an industry.
    industry_codes: Mapped[list[Any] | None]


class IdeaSignal(Base):
    """Which Idea a Signal belongs to. A Signal belongs to at most one Idea."""

    __tablename__ = "idea_signals"

    signal_id: Mapped[int] = mapped_column(
        ForeignKey("signals.id", ondelete="CASCADE"), primary_key=True
    )
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    similarity: Mapped[float | None]  # to the centroid when it attached; NULL if set by hand
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdeaRelation(Base):
    """Related: same problem, different Personas. Each pair is stored once, lower id first."""

    __tablename__ = "idea_relations"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    related_idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    similarity: Mapped[float | None]


class FitProfile(Base):
    """Your criteria for Ideas you'd build (GLOSSARY.md). One row; `version` goes up on each change
    so Ideas checked against an older version are checked again."""

    __tablename__ = "fit_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    skills: Mapped[list[Any]] = mapped_column(server_default="[]")
    price_min_usd: Mapped[int | None]  # per customer per month
    price_max_usd: Mapped[int | None]
    sales_model: Mapped[str | None] = mapped_column(String(16))  # self_serve / sales_led / either
    preferred_industries: Mapped[list[Any]] = mapped_column(server_default="[]")
    avoided_industries: Mapped[list[Any]] = mapped_column(server_default="[]")
    deal_breakers: Mapped[list[Any]] = mapped_column(server_default="[]")
    revenue_goal_usd: Mapped[int | None]  # monthly
    version: Mapped[int] = mapped_column(server_default="1")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProposedDecision(Base):
    """A suggested Review outcome (or kill) for an Idea. Never applied without you."""

    __tablename__ = "proposed_decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # review / kill
    proposal: Mapped[str] = mapped_column(String(16))  # working / archive
    archive_reason: Mapped[str | None] = mapped_column(String(32))
    rationale: Mapped[str] = mapped_column(Text)
    confidence: Mapped[float]  # 0-1
    status: Mapped[str] = mapped_column(String(16), server_default="pending")
    model: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IdeaReview(Base):
    """Something that changed an Idea's state, by you or automatically, with the evidence at the
    time (resurfacing compares against it)."""

    __tablename__ = "idea_reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    # published, to_working, to_archive, restored, resurfaced, auto_outside_fit, merged, split
    action: Mapped[str] = mapped_column(String(24))
    archive_reason: Mapped[str | None] = mapped_column(String(32))
    overrode_proposal: Mapped[bool | None]
    note: Mapped[str | None] = mapped_column(Text)
    score_at_action: Mapped[float | None]
    signal_count_at_action: Mapped[int | None]
    recent_signals_at_action: Mapped[int | None]  # Signals in the 4 weeks before the action
    community_ids_at_action: Mapped[list[Any]] = mapped_column(server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class IdeaSnapshot(Base):
    """An Idea's evidence and score at the start of a week, for trends."""

    __tablename__ = "idea_snapshots"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    week: Mapped[date] = mapped_column(Date, primary_key=True)  # Monday
    signal_count: Mapped[int]
    author_count: Mapped[int]
    community_count: Mapped[int]
    score: Mapped[float | None]


class IdeaCompetitor(Base):
    """A Competitor a Briefing named for an Idea (ones named in Signals link via the Signals)."""

    __tablename__ = "idea_competitors"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    competitor_id: Mapped[int] = mapped_column(
        ForeignKey("competitors.id", ondelete="CASCADE"), primary_key=True
    )


class Briefing(Base):
    """A written analysis of an Idea (PLAN.md §4). Each generation is a new version."""

    __tablename__ = "briefings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    version: Mapped[int]
    status: Mapped[str] = mapped_column(String(24))  # awaiting_response / complete
    stale: Mapped[bool] = mapped_column(Boolean, server_default="false")
    stale_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider: Mapped[str] = mapped_column(String(16))  # manual / anthropic
    format_version: Mapped[int]
    prompt_text: Mapped[str] = mapped_column(Text)
    frontmatter: Mapped[dict[str, Any] | None]
    content_md: Mapped[str | None] = mapped_column(Text)
    raw_response: Mapped[str | None] = mapped_column(Text)
    warnings: Mapped[list[Any]] = mapped_column(server_default="[]")
    signal_count_at_gen: Mapped[int | None]
    community_ids_at_gen: Mapped[list[Any]] = mapped_column(server_default="[]")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MarketStat(Base):
    """US business counts per industry (Census County Business Patterns), for market math."""

    __tablename__ = "market_stats"

    naics_code: Mapped[str] = mapped_column(String(6), primary_key=True)  # 2-6 digits
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    label: Mapped[str] = mapped_column(Text)
    establishments: Mapped[int]
    geography: Mapped[str] = mapped_column(String(8), server_default="US")


class WorkingItem(Base):
    """An Idea on the Working List: its Stage, Time limit and priority (PLAN.md §5)."""

    __tablename__ = "working_items"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    stage: Mapped[str] = mapped_column(String(16))  # researching / validating / building / paused
    paused_from: Mapped[str | None] = mapped_column(String(16))  # the Stage to resume into
    stage_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    time_limit_days: Mapped[int]
    extended_in_stage: Mapped[bool] = mapped_column(Boolean, server_default="false")
    priority_rank: Mapped[int]  # 1 = highest; set by Head-to-heads
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Hypothesis(Base):
    """Who would pay how much to have the problem solved, with Kill criteria. Edits make a new
    row; `current` marks the one in use."""

    __tablename__ = "hypotheses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    statement: Mapped[str] = mapped_column(Text)
    kill_criteria: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16))  # briefing / you
    current: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Experiment(Base):
    """One test of a Hypothesis, ending in an Outcome."""

    __tablename__ = "experiments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    hypothesis_id: Mapped[int | None] = mapped_column(ForeignKey("hypotheses.id"))
    stage: Mapped[str] = mapped_column(String(16))
    type: Mapped[str] = mapped_column(String(16))  # interviews / landing_page / pre_sale / other
    plan_md: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(String(16))  # pass / fail / inconclusive
    evidence_md: Mapped[str | None] = mapped_column(Text)
    met_kill_criteria: Mapped[bool] = mapped_column(Boolean, server_default="false")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Lead(Base):
    """An Author behind one of an Idea's Signals, worth contacting. Only what they posted
    publicly is stored; contact is always manual."""

    __tablename__ = "leads"
    __table_args__ = (UniqueConstraint("idea_id", "signal_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id", ondelete="CASCADE"))
    author: Mapped[str] = mapped_column(String(300))
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    url: Mapped[str] = mapped_column(Text)
    opener: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="to_contact")
    notes_md: Mapped[str | None] = mapped_column(Text)
    experiment_id: Mapped[int | None] = mapped_column(
        ForeignKey("experiments.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class HeadToHead(Base):
    """A choice between two Ideas about which deserves your time next. Undecided while
    `winner` is NULL."""

    __tablename__ = "head_to_heads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_a: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"))
    idea_b: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"))
    trigger: Mapped[str] = mapped_column(String(16))  # full_list / periodic
    winner: Mapped[int | None] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"))
    loser_action: Mapped[str | None] = mapped_column(String(16))  # paused / archived / kept
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ValidationKit(Base):
    """Material for testing an Idea, written when it reaches Validating: {"interview_script":
    [...], "lead_messages": {lead_id: text}, "landing_page": {...}, "offer": {...}}."""

    __tablename__ = "validation_kits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    idea_id: Mapped[int] = mapped_column(ForeignKey("ideas.id", ondelete="CASCADE"), index=True)
    content: Mapped[dict[str, Any]]
    model: Mapped[str] = mapped_column(String(200))
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class IdeaTrend(Base):
    """Google search interest for an Idea's search terms (Google Trends, weekly, 0-100 scaled).

    Feeds the Momentum part of the score: the last 4 complete weeks against the 4 before.
    """

    __tablename__ = "idea_trends"

    idea_id: Mapped[int] = mapped_column(
        ForeignKey("ideas.id", ondelete="CASCADE"), primary_key=True
    )
    terms: Mapped[list[Any]]
    geo: Mapped[str] = mapped_column(String(8))
    weeks: Mapped[list[Any]]  # [{"week": "2026-11-01", "values": {term: 0-100}}], oldest first
    ratio: Mapped[float | None]  # (last 4 weeks + 1) / (4 before + 1); None when all zero
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class EvalItem(Base):
    """An Item in the test set, with its answer-key labels."""

    __tablename__ = "eval_items"

    item_id: Mapped[int] = mapped_column(
        ForeignKey("items.id", ondelete="CASCADE"), primary_key=True
    )
    has_signal: Mapped[bool | None] = mapped_column(Boolean)
    kinds: Mapped[list[Any]] = mapped_column(server_default="[]")
    note: Mapped[str | None] = mapped_column(Text)
    labelled_by: Mapped[str | None] = mapped_column(String(16))  # claude / you
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EvalResult(Base):
    """One model's answer for one test Item."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run: Mapped[str] = mapped_column(String(64), index=True)  # e.g. "qwen3.6-35b-a3b@2026-11-21"
    model: Mapped[str] = mapped_column(String(200))
    item_id: Mapped[int] = mapped_column(ForeignKey("items.id", ondelete="CASCADE"))
    predicted_signal: Mapped[bool]
    predicted_kinds: Mapped[list[Any]] = mapped_column(server_default="[]")
    output: Mapped[dict[str, Any]]
    seconds: Mapped[float]
    output_tokens: Mapped[int | None]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CollectorCursor(Base):
    """How far each collector stream has collected, so the next run continues from there."""

    __tablename__ = "collector_cursors"

    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), primary_key=True)
    stream: Mapped[str] = mapped_column(String(300), primary_key=True)
    cursor: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Run(Base):
    """One run of one pipeline stage for one Source, with counts and errors."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stage: Mapped[str] = mapped_column(String(32))
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    status: Mapped[str] = mapped_column(String(16))  # running / succeeded / partial / failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stats: Mapped[dict[str, Any]] = mapped_column(server_default="{}")
    errors: Mapped[list[Any]] = mapped_column(server_default="[]")
