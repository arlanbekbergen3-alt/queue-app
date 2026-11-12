from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class SessionStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"


class QueueSessionORM(Base):
    __tablename__ = "queue_sessions"
    __table_args__ = (
        CheckConstraint("status IN ('open', 'closed')", name="ck_queue_sessions_status"),
        CheckConstraint("avg_service_seconds > 0", name="ck_queue_sessions_avg_positive"),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    group_name: Mapped[str | None] = mapped_column(String(60), nullable=True)
    assignment_id: Mapped[str | None] = mapped_column(
        ForeignKey("assignments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    code: Mapped[str] = mapped_column(String(8), unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default=SessionStatus.OPEN, nullable=False)
    next_ticket: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    avg_service_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
