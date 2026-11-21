"""Health: is the daily routine actually happening? Used by the Health page.

Complements `status.py` (config, database, workers). Each check turns into a plain-language
warning with the fix when something needs attention.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx2
from sqlalchemy import create_engine, text

from ideafinder.backup import BackupFile, list_backups
from ideafinder.config import Config
from ideafinder.settings import Settings

DAILY_LATE_AFTER = timedelta(hours=36)
BACKUP_LATE_AFTER = timedelta(hours=36)


@dataclass(frozen=True)
class LastRun:
    status: str
    finished_at: datetime | None
    stats: dict[str, Any]
    errors: list[Any]


@dataclass
class Health:
    daily: LastRun | None = None
    backup_run: LastRun | None = None
    latest_backup: BackupFile | None = None
    backup_count: int = 0
    lm_studio: bool = False
    lm_models: list[str] = field(default_factory=list)
    queue_backlog: dict[str, int] = field(default_factory=dict)
    failed_jobs: list[tuple[str, int]] = field(default_factory=list)
    unextracted_items: int = 0
    unembedded_signals: int = 0
    pooled_signals: int = 0
    schedule: dict[str, bool] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _last_run(conn, stage: str) -> LastRun | None:
    row = conn.execute(
        text(
            "SELECT status, finished_at, stats, errors FROM runs WHERE stage = :s "
            "ORDER BY started_at DESC LIMIT 1"
        ),
        {"s": stage},
    ).first()
    return LastRun(row.status, row.finished_at, row.stats or {}, row.errors or []) if row else None


def _lm_studio(config: Config | None) -> tuple[bool, list[str]]:
    if config is None:
        return False, []
    base = config.models.providers["lmstudio"].base_url
    try:
        response = httpx2.get(f"{base.rstrip('/')}/models", timeout=2)
        response.raise_for_status()
        return True, [m["id"] for m in response.json().get("data", [])]
    except (httpx2.HTTPError, ValueError):
        return False, []


def get_health(settings: Settings, config: Config | None, now: datetime | None = None) -> Health:
    from ideafinder.schedule import JOBS, is_loaded, plist_path

    now = now or datetime.now(UTC)
    h = Health()
    engine = create_engine(settings.sqlalchemy_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            h.daily = _last_run(conn, "daily")
            h.backup_run = _last_run(conn, "backup")
            h.queue_backlog = dict(
                conn.execute(
                    text(
                        "SELECT queue_name, count(*) FROM procrastinate_jobs "
                        "WHERE status IN ('todo', 'doing') GROUP BY 1"
                    )
                ).all()
            )
            h.failed_jobs = [
                tuple(r)
                for r in conn.execute(
                    text(
                        "SELECT task_name, count(*) FROM procrastinate_jobs "
                        "WHERE status = 'failed' GROUP BY 1 ORDER BY 2 DESC"
                    )
                ).all()
            ]
            h.unextracted_items = conn.scalar(
                text("SELECT count(*) FROM items WHERE prefilter = 'keep' AND extracted_at IS NULL")
            )
            h.unembedded_signals = conn.scalar(
                text("SELECT count(*) FROM signals WHERE embedding IS NULL")
            )
            h.pooled_signals = conn.scalar(
                text(
                    "SELECT count(*) FROM signals s LEFT JOIN idea_signals x ON x.signal_id = s.id "
                    "WHERE x.signal_id IS NULL AND s.embedding IS NOT NULL AND s.kind <> 'market'"
                )
            )
    finally:
        engine.dispose()

    backups = list_backups(settings.backup_dir)
    h.latest_backup = backups[0] if backups else None
    h.backup_count = len(backups)
    h.lm_studio, h.lm_models = _lm_studio(config)
    h.schedule = {job.command: plist_path(job).exists() and is_loaded(job) for job in JOBS}

    # Warnings, each with the fix.
    if not all(h.schedule.values()):
        h.warnings.append(
            "The daily schedule isn't installed, so nothing runs unless the app is open. "
            "Run: ideafinder schedule install"
        )
    if h.daily is None or h.daily.finished_at is None:
        h.warnings.append("The daily run has never finished. Run: ideafinder daily")
    elif now - h.daily.finished_at > DAILY_LATE_AFTER:
        h.warnings.append(
            f"The last daily run finished {h.daily.finished_at:%-d %b %H:%M}. Is the Mac asleep at "
            "6:00 every day? It catches up when it wakes; or run: ideafinder daily"
        )
    elif h.daily.errors:
        h.warnings.append("The last daily run had problems: " + "; ".join(map(str, h.daily.errors)))
    if h.latest_backup is None:
        h.warnings.append("There's no backup yet. Run: ideafinder backup")
    elif now - h.latest_backup.taken_at > BACKUP_LATE_AFTER:
        h.warnings.append(
            f"The newest backup is from {h.latest_backup.taken_at:%-d %b}. Run: ideafinder backup"
        )
    if h.backup_run and h.backup_run.status == "failed":
        h.warnings.append("The last backup failed: " + "; ".join(map(str, h.backup_run.errors)))
    if not h.lm_studio:
        h.warnings.append(
            "LM Studio isn't reachable, so extraction, naming, Summaries and proposals wait. "
            "Start it with: lms server start"
        )
    elif config:
        wanted = {r.model for r in config.models.tasks.values() if r.provider == "lmstudio"}
        missing = sorted(m for m in wanted if m and m not in h.lm_models)
        if missing:
            h.warnings.append(
                f"LM Studio doesn't have {', '.join(missing)}. Download it with: lms get "
                f"{missing[0]}"
            )
    if h.failed_jobs:
        h.warnings.append(
            "Some background jobs failed: "
            + ", ".join(f"{name} ({n})" for name, n in h.failed_jobs)
            + ". See the worker's log."
        )
    return h
