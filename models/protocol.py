from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base


def _now() -> datetime:
    return datetime.now(UTC)


class SubmissionStatus(StrEnum):
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class AssignmentORM(Base):
    __tablename__ = "assignments"
    __table_args__ = (
        UniqueConstraint("teacher_id", "title", name="uq_assignments_teacher_title"),
        CheckConstraint(
            "questions_per_submission BETWEEN 1 AND 10", name="ck_assignments_questions_count"
        ),
    )

    teacher_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)
    questions_per_submission: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    practice_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_template: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class QuestionORM(Base):
    __tablename__ = "assignment_questions"
    __table_args__ = (
        UniqueConstraint("assignment_id", "text", name="uq_questions_assignment_text"),
        CheckConstraint("difficulty BETWEEN 1 AND 3", name="ck_questions_difficulty"),
    )

    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    topic: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class CriterionORM(Base):
    __tablename__ = "assignment_criteria"
    __table_args__ = (
        UniqueConstraint("assignment_id", "name", name="uq_criteria_assignment_name"),
        CheckConstraint("max_points BETWEEN 1 AND 100", name="ck_criteria_max_points"),
    )

    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("assignments.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    max_points: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class SubmissionORM(Base):
    __tablename__ = "submissions"
    __table_args__ = (
        UniqueConstraint("entry_id", name="uq_submissions_entry"),
        CheckConstraint(
            "status IN ('in_progress', 'completed', 'cancelled')", name="ck_submissions_status"
        ),
        Index("ix_submissions_session_status", "session_id", "status"),
        Index("ix_submissions_assignment_status", "assignment_id", "status"),
    )

    entry_id: Mapped[str] = mapped_column(
        ForeignKey("entries.id", ondelete="CASCADE"), nullable=False
    )
    session_id: Mapped[str] = mapped_column(
        ForeignKey("queue_sessions.id", ondelete="CASCADE"), nullable=False
    )
    assignment_id: Mapped[str] = mapped_column(
        ForeignKey("assignments.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(16), default=SubmissionStatus.IN_PROGRESS, nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_points: Mapped[int | None] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(String(1000), default="", nullable=False)
    attempt_number: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    selection_usage: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=True
    )
    selection_seed: Mapped[str] = mapped_column(String(32), nullable=False)
    pool_question_ids: Mapped[list[Any]] = mapped_column(
        JSON().with_variant(JSONB(), "postgresql"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )


class SubmissionQuestionORM(Base):
    __tablename__ = "submission_questions"
    __table_args__ = (
        UniqueConstraint("submission_id", "position", name="uq_submission_questions_position"),
        Index(
            "uq_submission_questions_question",
            "submission_id",
            "question_id",
            unique=True,
            postgresql_where=text("question_id IS NOT NULL"),
        ),
    )

    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[str | None] = mapped_column(
        ForeignKey("assignment_questions.id", ondelete="SET NULL"), nullable=True
    )
    text: Mapped[str] = mapped_column(String(1000), nullable=False)
    difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    topic: Mapped[str] = mapped_column(String(60), default="", nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)


class ScoreORM(Base):
    __tablename__ = "submission_scores"
    __table_args__ = (
        UniqueConstraint("submission_id", "criterion_id", name="uq_scores_submission_criterion"),
        CheckConstraint("points >= 0", name="ck_scores_points"),
    )

    submission_id: Mapped[str] = mapped_column(
        ForeignKey("submissions.id", ondelete="CASCADE"), nullable=False
    )
    criterion_id: Mapped[str] = mapped_column(
        ForeignKey("assignment_criteria.id", ondelete="RESTRICT"), nullable=False
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(String(300), default="", nullable=False)


class AuditLogORM(Base):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_submission_seq", "submission_id", "seq"),
        CheckConstraint(
            "action <> 'correct' OR length(trim(reason)) > 0", name="ck_audit_log_reason"
        ),
    )

    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, nullable=False
    )
    submission_id: Mapped[str] = mapped_column(String(36), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    field: Mapped[str] = mapped_column(String(80), nullable=False)
    old_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    reason: Mapped[str] = mapped_column(String(300), default="", nullable=False)


AUDIT_FUNCTION_SQL = """
CREATE OR REPLACE FUNCTION audit_log_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only';
END;
$$ LANGUAGE plpgsql
"""
AUDIT_TRIGGER_SQL = """
CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
"""

event.listen(AuditLogORM.__table__, "after_create", DDL(AUDIT_FUNCTION_SQL))
event.listen(AuditLogORM.__table__, "after_create", DDL(AUDIT_TRIGGER_SQL))
