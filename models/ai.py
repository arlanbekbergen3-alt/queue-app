from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class AIJobORM(Base):
    __tablename__ = "ai_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'running', 'done', 'error')", name="ck_ai_jobs_status"
        ),
        Index("ix_ai_jobs_teacher_created", "teacher_id", "created_at"),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.QUEUED, nullable=False)
    result: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
