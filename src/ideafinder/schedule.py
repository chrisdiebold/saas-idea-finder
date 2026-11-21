"""launchd schedules (ADR 0003): the daily chain and the nightly backup, without the web app open.

The plists are generated rather than kept as files because they need absolute paths (uv, this
project, the log folder) that differ per machine. macOS runs a calendar job it missed while the
Mac was asleep as soon as it wakes.
"""

import os
import plistlib
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ideafinder.settings import PROJECT_ROOT, Settings

LAUNCH_AGENTS = Path.home() / "Library" / "LaunchAgents"


@dataclass(frozen=True)
class Job:
    label: str
    command: str  # the ideafinder subcommand
    hour: int
    minute: int
    what: str


JOBS = (
    Job("com.ideafinder.daily", "daily", 6, 0, "collect, extract, cluster and rank"),
    Job("com.ideafinder.backup", "backup", 2, 30, "back up the database"),
)


def uv_path() -> str:
    found = shutil.which("uv") or str(Path.home() / ".local" / "bin" / "uv")
    return found


def plist_for(job: Job, settings: Settings, uv: str | None = None) -> dict:
    log = settings.log_dir / f"{job.command}.log"
    return {
        "Label": job.label,
        "ProgramArguments": [uv or uv_path(), "run", "--project", str(PROJECT_ROOT), "ideafinder",
                             job.command],
        "WorkingDirectory": str(PROJECT_ROOT),
        "StartCalendarInterval": {"Hour": job.hour, "Minute": job.minute},
        "StandardOutPath": str(log),
        "StandardErrorPath": str(log),
        # launchd starts jobs with a bare PATH; uv and Postgres.app's tools need theirs.
        "EnvironmentVariables": {
            "PATH": f"{Path(uv or uv_path()).parent}:{settings.pg_bin_dir}:/usr/bin:/bin"
        },
        "ProcessType": "Background",
    }  # fmt: skip


def plist_path(job: Job) -> Path:
    return LAUNCH_AGENTS / f"{job.label}.plist"


def _domain() -> str:
    return f"gui/{os.getuid()}"


def is_loaded(job: Job) -> bool:
    result = subprocess.run(
        ["launchctl", "print", f"{_domain()}/{job.label}"], capture_output=True, text=True
    )
    return result.returncode == 0


def install(settings: Settings) -> list[Path]:
    """Write the plists and load them (replacing older versions)."""
    LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
    settings.log_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for job in JOBS:
        path = plist_path(job)
        if is_loaded(job):
            subprocess.run(["launchctl", "bootout", f"{_domain()}/{job.label}"], check=False)
        path.write_bytes(plistlib.dumps(plist_for(job, settings)))
        subprocess.run(["launchctl", "bootstrap", _domain(), str(path)], check=True)
        written.append(path)
    return written


def uninstall() -> list[Path]:
    removed = []
    for job in JOBS:
        if is_loaded(job):
            subprocess.run(["launchctl", "bootout", f"{_domain()}/{job.label}"], check=False)
        path = plist_path(job)
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
