from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, text
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
        CheckConstraint("order_mode IN ('join', 'lottery')", name="ck_queue_sessions_order_mode"),
        CheckConstraint("ready_bonus_points BETWEEN 0 AND 5", name="ck_queue_sessions_ready_bonus"),
        CheckConstraint(
            "ready_skip_questions BETWEEN 0 AND 1", name="ck_queue_sessions_ready_skip"
        ),
        CheckConstraint(
            "session_mode IN ('queue_only', 'quick', 'protocol')",
            name="ck_queue_sessions_session_mode",
        ),
        CheckConstraint("quick_max_points BETWEEN 1 AND 100", name="ck_queue_sessions_quick_max"),
        CheckConstraint(
            "presence_check IN ('off', 'rotating_code')", name="ck_queue_sessions_presence"
        ),
        CheckConstraint(
            "presence_interval BETWEEN 30 AND 60", name="ck_queue_sessions_presence_interval"
        ),
        CheckConstraint(
            "questions_override IS NULL OR questions_override BETWEEN 1 AND 10",
            name="ck_queue_sessions_questions_override",
        ),
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
    join_open: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    roster_only: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    order_mode: Mapped[str] = mapped_column(
        String(16), default="join", server_default=text("'join'"), nullable=False
    )
    ready_priority: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    ready_bonus_points: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    ready_skip_questions: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    session_mode: Mapped[str] = mapped_column(
        String(12), default="protocol", server_default=text("'protocol'"), nullable=False
    )
    quick_max_points: Mapped[int] = mapped_column(
        Integer, default=10, server_default=text("10"), nullable=False
    )
    presence_check: Mapped[str] = mapped_column(
        String(16), default="off", server_default=text("'off'"), nullable=False
    )
    presence_interval: Mapped[int] = mapped_column(
        Integer, default=30, server_default=text("30"), nullable=False
    )
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    questions_override: Mapped[int | None] = mapped_column(Integer, nullable=True)
    budget_declined: Mapped[int | None] = mapped_column(Integer, nullable=True)
    lottery_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_ticket: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    avg_service_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
