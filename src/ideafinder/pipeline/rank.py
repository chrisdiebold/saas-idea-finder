"""The daily ranking run: score → re-check fit → publish → resurface → propose → snapshot."""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ideafinder.config import ScoringConfig
from ideafinder.llm import LLM
from ideafinder.pipeline.fit import get_profile
from ideafinder.pipeline.propose import ProposeReport, propose_missing
from ideafinder.pipeline.publish import PublishReport, publish_ready
from ideafinder.pipeline.resurface import RefitReport, refit, resurface_on_evidence
from ideafinder.pipeline.score import score_ideas, snapshot_week


@dataclass
class RankReport:
    scored: int
    has_profile: bool
    refit: RefitReport
    publish: PublishReport
    resurfaced: list[int]
    propose: ProposeReport
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
        await score_ideas(session, config, now=now)  # fit checks changed some scores
    proposed = await propose_missing(session, llm, profile)
    snapshots = await snapshot_week(session, now)
    return RankReport(
        scored=len(evidence),
        has_profile=profile is not None,
        refit=refitted,
        publish=published,
        resurfaced=resurfaced,
        propose=proposed,
        snapshots=snapshots,
    )
