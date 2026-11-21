"""System status: config, database and workers. Used by the status page and `ideafinder doctor`."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine, text

from ideafinder.config import Config, ConfigError, load_config
from ideafinder.db import admin
from ideafinder.settings import Settings
from ideafinder.worker import QUEUES

HEARTBEAT_FRESH = timedelta(minutes=3)


@dataclass(frozen=True)
class QueueStatus:
    queue: str
    last_seen: datetime | None

    @property
    def alive(self) -> bool:
        return self.last_seen is not None and datetime.now(UTC) - self.last_seen < HEARTBEAT_FRESH


@dataclass(frozen=True)
class SourceRun:
    """The latest collection run for one Source."""

    source: str
    name: str
    status: str
    finished_at: datetime | None
    new: int
    errors: list[str]
    items_total: int


@dataclass(frozen=True)
class SystemStatus:
    config: Config | None
    config_error: str | None
    database: admin.DatabaseStatus
    queues: list[QueueStatus] = field(default_factory=list)
    source_runs: dict[str, SourceRun] = field(default_factory=dict)

    @property
    def problems(self) -> list[str]:
        """Plain-language problems, each with the fix."""
        out = []
        if self.config_error:
            out.append(f"Config is invalid: {self.config_error}")
        if not self.database.reachable:
            out.append(
                "Can't reach Postgres. Open Postgres.app and start the server "
                f"({self.database.error})"
            )
        elif not self.database.up_to_date:
            out.append("The database isn't up to date. Run: ideafinder db upgrade")
        return out


def _heartbeats(settings: Settings) -> dict[str, datetime]:
    engine = create_engine(settings.sqlalchemy_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT queue, last_seen FROM worker_heartbeats")).all()
        return {queue: last_seen for queue, last_seen in rows}
    finally:
        engine.dispose()


_LATEST_RUNS = text("""
    SELECT s.key, s.name, r.status, r.finished_at,
           coalesce((r.stats -> 'totals' ->> 'new')::int, 0) AS new, r.errors,
           (SELECT count(*) FROM items i WHERE i.source_id = s.id) AS items_total
    FROM sources s
    LEFT JOIN LATERAL (
        SELECT * FROM runs WHERE runs.source_id = s.id AND runs.stage = 'collect'
        ORDER BY started_at DESC LIMIT 1
    ) r ON true
    WHERE r.id IS NOT NULL
""")


def _source_runs(settings: Settings) -> dict[str, SourceRun]:
    engine = create_engine(settings.sqlalchemy_url, connect_args={"connect_timeout": 3})
    try:
        with engine.connect() as conn:
            return {
                row.key: SourceRun(
                    source=row.key,
                    name=row.name,
                    status=row.status,
                    finished_at=row.finished_at,
                    new=row.new,
                    errors=list(row.errors or []),
                    items_total=row.items_total,
                )
                for row in conn.execute(_LATEST_RUNS)
            }
    finally:
        engine.dispose()


def get_status(settings: Settings) -> SystemStatus:
    try:
        config, config_error = load_config(settings.config_dir), None
    except ConfigError as exc:
        config, config_error = None, str(exc)

    database = admin.status(settings)
    heartbeats = _heartbeats(settings) if database.up_to_date else {}
    queues = [QueueStatus(queue=q, last_seen=heartbeats.get(q)) for q in QUEUES]
    source_runs = _source_runs(settings) if database.up_to_date else {}
    return SystemStatus(
        config=config,
        config_error=config_error,
        database=database,
        queues=queues,
        source_runs=source_runs,
    )
