"""Nightly backups (PLAN.md §2 Runtime): a compressed `pg_dump`, keeping 14 days.

Backups go to `backup_dir` (default: `backups/` in the project, inside your home folder so Time
Machine covers it; point it at iCloud Drive in `.env` if you prefer). Files are named by time,
and old ones are removed by the time in their name, not their modification date, so copying a
backup elsewhere doesn't change what's kept.

Restore with:  pg_restore --clean --if-exists --dbname ideafinder <file>
"""

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine, text

from ideafinder.settings import Settings

NAME = re.compile(r"^ideafinder-(\d{8}-\d{6})\.dump$")


class BackupError(Exception):
    """The backup couldn't be made; the message says how to fix it."""


@dataclass(frozen=True)
class BackupFile:
    path: Path
    taken_at: datetime
    size: int


def pg_dump_path(settings: Settings) -> Path:
    bundled = settings.pg_bin_dir / "pg_dump"
    if bundled.exists():
        return bundled
    found = shutil.which("pg_dump")
    if found is None:
        raise BackupError(
            f"Can't find pg_dump in {settings.pg_bin_dir} or on PATH. Is Postgres.app installed? "
            "Set IDEAFINDER_PG_BIN_DIR to its bin folder if it's elsewhere."
        )
    return Path(found)


def list_backups(directory: Path) -> list[BackupFile]:
    """Backups in the folder, newest first."""
    if not directory.exists():
        return []
    out = []
    for path in directory.iterdir():
        match = NAME.match(path.name)
        if match:
            taken = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").replace(tzinfo=UTC)
            out.append(BackupFile(path, taken, path.stat().st_size))
    return sorted(out, key=lambda b: b.taken_at, reverse=True)


def prune(directory: Path, keep_days: int, now: datetime) -> list[Path]:
    """Delete backups older than `keep_days`, but never the newest one."""
    backups = list_backups(directory)
    cutoff = now - timedelta(days=keep_days)
    removed = []
    for b in backups[1:]:
        if b.taken_at < cutoff:
            b.path.unlink()
            removed.append(b.path)
    return removed


def _record(settings: Settings, status: str, stats: dict, errors: list[str]) -> None:
    engine = create_engine(settings.sqlalchemy_url, connect_args={"connect_timeout": 3})
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO runs (stage, status, started_at, finished_at, stats, errors) "
                    "VALUES ('backup', :status, now(), now(), CAST(:stats AS jsonb), "
                    "CAST(:errors AS jsonb))"
                ),
                {"status": status, "stats": _json(stats), "errors": _json(errors)},
            )
    finally:
        engine.dispose()


def _json(value) -> str:
    return json.dumps(value, default=str)


def backup(settings: Settings, now: datetime | None = None) -> tuple[BackupFile, list[Path]]:
    """Dump the database, then remove expired backups. Returns the new file and what was removed."""
    now = now or datetime.now(UTC)
    directory = settings.backup_dir
    try:
        directory.mkdir(parents=True, exist_ok=True)
        target = directory / f"ideafinder-{now:%Y%m%d-%H%M%S}.dump"
        partial = target.with_suffix(".partial")
        result = subprocess.run(
            [
                str(pg_dump_path(settings)),
                "--format=custom",  # compressed, and restorable table by table
                "--no-owner",
                f"--file={partial}",
                f"--dbname={settings.database_url}",
            ],
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            partial.unlink(missing_ok=True)
            raise BackupError(f"pg_dump failed: {result.stderr.strip()[:500]}")
        partial.rename(target)  # only complete dumps get a backup name
        removed = prune(directory, settings.backup_keep_days, now)
    except (BackupError, OSError, subprocess.TimeoutExpired) as exc:
        _record(settings, "failed", {}, [str(exc)])
        if isinstance(exc, BackupError):
            raise
        raise BackupError(str(exc)) from exc
    made = BackupFile(target, now, target.stat().st_size)
    _record(
        settings,
        "succeeded",
        {"file": str(target), "bytes": made.size, "removed": [p.name for p in removed]},
        [],
    )
    return made, removed
