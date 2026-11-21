"""SQLAlchemy models. Tables are added milestone by milestone (see PLAN.md §6)."""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class WorkerHeartbeat(Base):
    """Last time a worker processed a job on each queue. Shown on the status page."""

    __tablename__ = "worker_heartbeats"

    queue: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
