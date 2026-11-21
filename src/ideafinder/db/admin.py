"""Database administration: create the database, apply migrations, report status.

Migrations run through a synchronous psycopg connection, which keeps Alembic simple.
"""

from dataclasses import dataclass
from pathlib import Path

import psycopg
from alembic import command
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import sql
from sqlalchemy import create_engine

from ideafinder.settings import Settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def alembic_config(settings: Settings) -> AlembicConfig:
    cfg = AlembicConfig()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    # '%' must be escaped for Alembic's ConfigParser.
    cfg.set_main_option("sqlalchemy.url", settings.sqlalchemy_url.replace("%", "%%"))
    return cfg


def create_database_if_missing(settings: Settings) -> bool:
    """Create the configured database if it doesn't exist. Returns True if it was created."""
    conninfo = psycopg.conninfo.conninfo_to_dict(settings.database_url)
    dbname = conninfo.pop("dbname")
    with psycopg.connect(**conninfo, dbname="postgres", autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_database WHERE datname = %s", (dbname,)).fetchone()
        if exists:
            return False
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
        return True


def upgrade(settings: Settings, revision: str = "head") -> None:
    command.upgrade(alembic_config(settings), revision)


def new_revision(settings: Settings, message: str, autogenerate: bool = True) -> None:
    command.revision(alembic_config(settings), message=message, autogenerate=autogenerate)


@dataclass(frozen=True)
class DatabaseStatus:
    reachable: bool
    error: str | None = None
    server_version: str | None = None
    pgvector_version: str | None = None
    current_revision: str | None = None
    head_revision: str | None = None

    @property
    def up_to_date(self) -> bool:
        return self.reachable and self.current_revision == self.head_revision


def head_revision(settings: Settings) -> str | None:
    return ScriptDirectory.from_config(alembic_config(settings)).get_current_head()


def status(settings: Settings) -> DatabaseStatus:
    head = head_revision(settings)
    try:
        engine = create_engine(settings.sqlalchemy_url, connect_args={"connect_timeout": 3})
        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
            server = conn.exec_driver_sql("SHOW server_version").scalar()
            vector = conn.exec_driver_sql(
                "SELECT extversion FROM pg_extension WHERE extname = 'vector'"
            ).scalar()
        engine.dispose()
    except Exception as exc:  # any failure here means "not usable", reported to the user
        return DatabaseStatus(reachable=False, error=str(exc).strip(), head_revision=head)
    return DatabaseStatus(
        reachable=True,
        server_version=server,
        pgvector_version=vector,
        current_revision=current,
        head_revision=head,
    )
