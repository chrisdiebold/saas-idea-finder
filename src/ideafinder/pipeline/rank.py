"""The daily ranking run: score → re-check fit → publish → resurface → summarise → map industries
→ propose → mark Briefings Stale → snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ScoringConfig
from ideafinder.llm import LLM
from ideafinder.pipeline.briefing import mark_stale
from ideafinder.pipeline.fit import get_profile
from ideafinder.pipeline.industry import map_industries
from ideafinder.pipeline.propose import ProposeReport, propose_missing
from ideafinder.pipeline.publish import PublishReport, publish_ready
from ideafinder.pipeline.resurface import RefitReport, refit, resurface_on_evidence
from ideafinder.pipeline.score import score_ideas, snapshot_week
from ideafinder.pipeline.summary import SummaryReport, summarize_pending
from ideafinder.validation.head_to_head import ask_periodic


@dataclass
class RankReport:
    scored: int
    has_profile: bool
    refit: RefitReport
    publish: PublishReport
    resurfaced: list[int]
    summaries: SummaryReport
    propose: ProposeReport
    industries_mapped: int
    stale_briefings: list[int]
    head_to_head_asked: bool
    snapshots: int


async def rank(
    session: AsyncSession, llm: LLM, config: ScoringConfig, now: datetime | None = None
) -> RankReport:
    now = now or datetime.now(UTC)
    profile = await get_profile(session)
    evidence = await score_ideas(session, config, now=now)
    refitted = await refit(session, llm, profile, evidence)
    published = await publish_ready(session, llm, config.publish, evidence, profile)
    resurfaced = await resurface_on_evidence(session, config.resurface, evidence)
    if refitted.checked or published.published:
        evidence = await score_ideas(session, config, now=now)  # fit checks changed some scores
    summaries = await summarize_pending(session, llm, config.stale, evidence)
    industries = await map_industries(session, llm)
    proposed = await propose_missing(session, llm, profile)
    stale = await mark_stale(session, config.stale, evidence)
    snapshots = await snapshot_week(session, now)
    asked = await ask_periodic(session, now)
    return RankReport(
        scored=len(evidence),
        has_profile=profile is not None,
        refit=refitted,
        publish=published,
        resurfaced=resurfaced,
        summaries=summaries,
        propose=proposed,
        industries_mapped=industries,
        stale_briefings=stale,
        head_to_head_asked=asked is not None,
        snapshots=snapshots,
    )
