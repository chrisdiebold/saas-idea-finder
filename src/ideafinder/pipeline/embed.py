"""Embeddings for Signals: problem statement plus Persona, so the same problem for different
people lands in different places (ADR 0001)."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.db.models import Signal
from ideafinder.llm import LLM

BATCH = 64
# nomic-embed-text expects a task prefix; "clustering:" is the one for grouping similar texts.
PREFIX = "clustering: "


def embedding_text(signal: Signal) -> str:
    return f"{PREFIX}{signal.problem_statement} (Persona: {signal.persona})"


async def embed_pending(session: AsyncSession, llm: LLM, limit: int = 10_000) -> int:
    done = 0
    while done < limit:
        signals = (
            await session.scalars(
                select(Signal).where(Signal.embedding.is_(None)).order_by(Signal.id).limit(BATCH)
            )
        ).all()
        if not signals:
            break
        vectors = await llm.embed([embedding_text(s) for s in signals])
        for signal, vector in zip(signals, vectors, strict=True):
            await session.execute(
                update(Signal).where(Signal.id == signal.id).values(embedding=vector)
            )
        await session.commit()
        done += len(signals)
    return done
