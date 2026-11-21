"""SQLAlchemy models. Tables are added milestone by milestone (see PLAN.md §6)."""

from datetime import datetime
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
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
