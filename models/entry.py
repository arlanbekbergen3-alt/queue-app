from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class EntryStatus(StrEnum):
    WAITING = "waiting"
    CALLED = "called"
    DONE = "done"
    SKIPPED = "skipped"
    LEFT = "left"


class EntryORM(Base):
    __tablename__ = "entries"
    __table_args__ = (
        UniqueConstraint("session_id", "ticket", name="uq_entries_session_ticket"),
        UniqueConstraint("session_id", "token_hash", name="uq_entries_session_token"),
        Index("ix_entries_session_status_sort", "session_id", "status", "sort_order"),
        Index(
            "uq_entries_waiting_sort_order",
            "session_id",
            "sort_order",
            unique=True,
            postgresql_where=text("status = 'waiting'"),
        ),
        Index(
            "uq_entries_one_called_per_session",
            "session_id",
            unique=True,
            postgresql_where=text("status = 'called'"),
        ),
        CheckConstraint(
            "status IN ('waiting', 'called', 'done', 'skipped', 'left')",
            name="ck_entries_status",
        ),
    )

    session_id: Mapped[str] = mapped_column(
        ForeignKey("queue_sessions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(60), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    ticket: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=EntryStatus.WAITING, nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
