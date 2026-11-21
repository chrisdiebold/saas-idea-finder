"""The `ideafinder` command line."""

import asyncio
import contextlib
import logging
from typing import Annotated

import typer
import uvicorn

from ideafinder.settings import get_settings

app = typer.Typer(no_args_is_help=True, help="Find and validate SaaS ideas from public evidence.")
db_app = typer.Typer(no_args_is_help=True, help="Create, migrate and inspect the database.")
app.add_typer(db_app, name="db")

QueueOption = Annotated[
    list[str] | None,
    typer.Option("--queue", "-q", help="Queue to serve (repeatable). Default: all queues."),
]


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("alembic").setLevel(logging.WARNING)  # its plugin setup is noisy at INFO


def _require_ready() -> None:
    """Stop with a clear message if the database isn't usable yet."""
    from ideafinder.status import get_status

    status = get_status(get_settings())
    if status.problems:
        for problem in status.problems:
            typer.secho(f"✗ {problem}", fg="red", err=True)
        raise typer.Exit(1)


def _resolve_queues(queues: list[str] | None) -> list[str]:
    from ideafinder.worker import QUEUES

    chosen = queues or list(QUEUES)
    unknown = set(chosen) - set(QUEUES)
    if unknown:
        raise typer.BadParameter(f"unknown queue(s): {sorted(unknown)}; choose from {QUEUES}")
    return chosen


def _web_server() -> uvicorn.Server:
    settings = get_settings()
    return uvicorn.Server(
        uvicorn.Config("ideafinder.web.app:app", host=settings.web_host, port=settings.web_port)
    )


async def _run_worker(queues: list[str]) -> None:
    from ideafinder.worker import WORKER_OPTIONS
    from ideafinder.worker import app as worker_app

    async with worker_app.open_async():
        await worker_app.run_worker_async(
            queues=queues, install_signal_handlers=False, **WORKER_OPTIONS
        )


async def _up(queues: list[str]) -> None:
    worker = asyncio.create_task(_run_worker(queues))
    try:
        await _web_server().serve()  # returns when you press Ctrl+C
    finally:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker


@app.command()
def up(queues: QueueOption = None) -> None:
    """Start the web app and the workers together (daily use)."""
    _configure_logging()
    _require_ready()
    chosen = _resolve_queues(queues)
    settings = get_settings()
    typer.echo(f"Web app: http://{settings.web_host}:{settings.web_port}  ·  queues: {chosen}")
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_up(chosen))


@app.command()
def web() -> None:
    """Start only the web app."""
    _configure_logging()
    with contextlib.suppress(KeyboardInterrupt):
        _web_server().run()


@app.command()
def worker(queues: QueueOption = None) -> None:
    """Start only the workers (used by scheduled jobs, and on the Pi later)."""
    _configure_logging()
    _require_ready()
    chosen = _resolve_queues(queues)
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(_run_worker(chosen))


@app.command()
def doctor() -> None:
    """Check config, database and workers, and say how to fix any problem."""
    from ideafinder.status import get_status

    status = get_status(get_settings())
    db = status.database

    def line(ok: bool, label: str, detail: str = "") -> None:
        typer.secho(
            f"{'✓' if ok else '✗'} {label}" + (f": {detail}" if detail else ""),
            fg="green" if ok else "red",
        )

    line(status.config is not None, "Config", status.config_error or "valid")
    line(db.reachable, "Postgres", db.server_version or db.error or "")
    if db.reachable:
        line(db.pgvector_version is not None, "pgvector", db.pgvector_version or "not installed")
        line(db.up_to_date, "Migrations", f"at {db.current_revision}, latest {db.head_revision}")
    for q in status.queues:
        line(q.alive, f"Worker '{q.queue}'", "running" if q.alive else "not running")
    if status.problems:
        typer.echo("")
        for problem in status.problems:
            typer.echo(f"→ {problem}")
        raise typer.Exit(1)


@db_app.command("upgrade")
def db_upgrade() -> None:
    """Create the database if needed and apply all migrations."""
    from ideafinder.db import admin

    settings = get_settings()
    if admin.create_database_if_missing(settings):
        typer.echo("Created the database.")
    admin.upgrade(settings)
    typer.secho("Database is up to date.", fg="green")


@db_app.command("status")
def db_status() -> None:
    """Show the database version, pgvector and migration state."""
    from ideafinder.db import admin

    s = admin.status(get_settings())
    if not s.reachable:
        typer.secho(f"Not reachable: {s.error}", fg="red")
        raise typer.Exit(1)
    typer.echo(f"Postgres {s.server_version}, pgvector {s.pgvector_version or 'not installed'}")
    typer.echo(f"Migration {s.current_revision or 'none'} (latest {s.head_revision})")


@db_app.command("revision")
def db_revision(
    message: Annotated[str, typer.Option("--message", "-m", help="What the migration does.")],
    empty: Annotated[bool, typer.Option(help="Don't autogenerate from the models.")] = False,
) -> None:
    """Create a new migration (autogenerated from the models by default)."""
    from ideafinder.db import admin

    admin.new_revision(get_settings(), message, autogenerate=not empty)
