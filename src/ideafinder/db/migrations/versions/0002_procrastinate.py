"""Create procrastinate's job-queue schema.

Revision ID: 0002
Revises: 0001

The SQL comes from the installed procrastinate package, which is pinned to an exact version in
pyproject.toml. When upgrading procrastinate, add a new migration that runs the package's own
migration files for the versions in between (see SchemaManager.get_migrations_path()); never
edit this one.
"""

from alembic import op
from procrastinate.schema import SchemaManager

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Run on the raw psycopg connection (same transaction): the schema contains ':' and '%',
    # which SQLAlchemy would treat as bind parameters and psycopg as placeholders.
    op.get_bind().connection.driver_connection.execute(SchemaManager.get_schema())


def downgrade() -> None:
    raise NotImplementedError("Dropping the job queue is not supported; recreate the database.")
