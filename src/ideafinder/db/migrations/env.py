from alembic import context
from sqlalchemy import engine_from_config, pool

from ideafinder.db.models import Base

config = context.config
target_metadata = Base.metadata

# procrastinate's tables are created by migration 0002 from its own schema, not from our models.
PROCRASTINATE_PREFIX = "procrastinate_"


def include_object(obj, name, type_, reflected, compare_to):
    return not (type_ == "table" and name and name.startswith(PROCRASTINATE_PREFIX))


def run_migrations_online() -> None:
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("Offline migrations are not supported; run against a live database.")
run_migrations_online()
