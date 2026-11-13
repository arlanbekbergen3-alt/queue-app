from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import Row, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.entry import EntryORM
from models.protocol import (
    AssignmentORM,
    AuditLogORM,
    CriterionORM,
    QuestionORM,
    ScoreORM,
    SubmissionORM,
    SubmissionQuestionORM,
    SubmissionStatus,
)
from models.queue_session import QueueSessionORM


class ProtocolRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_assignment(
        self, assignment_id: str, teacher_id: str, *, lock: bool = False
    ) -> AssignmentORM | None:
        stmt = select(AssignmentORM).where(
            AssignmentORM.id == assignment_id, AssignmentORM.teacher_id == teacher_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def lock_assignment_by_id(self, assignment_id: str) -> AssignmentORM | None:
        stmt = (
            select(AssignmentORM)
            .where(AssignmentORM.id == assignment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_assignments(self, teacher_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(AssignmentORM)
            .where(AssignmentORM.teacher_id == teacher_id, AssignmentORM.is_system.is_(False))
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def assignment_by_title(self, teacher_id: str, title: str) -> AssignmentORM | None:
        stmt = select(AssignmentORM).where(
            AssignmentORM.teacher_id == teacher_id, AssignmentORM.title == title
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def lock_teacher_assignments(self, teacher_id: str) -> None:
        await self.db.execute(
            select(func.pg_advisory_xact_lock(func.hashtext("assignments:" + teacher_id)))
        )

    async def assignment_titles(self, teacher_id: str) -> set[str]:
        stmt = select(AssignmentORM.title).where(AssignmentORM.teacher_id == teacher_id)
        return set((await self.db.execute(stmt)).scalars())

    async def list_templates(self, teacher_id: str) -> list[AssignmentORM]:
        stmt = (
            select(AssignmentORM)
            .where(AssignmentORM.teacher_id == teacher_id, AssignmentORM.is_template.is_(True))
            .order_by(AssignmentORM.title)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def list_assignments(self, teacher_id: str) -> list[AssignmentORM]:
        stmt = (
            select(AssignmentORM)
            .where(AssignmentORM.teacher_id == teacher_id, AssignmentORM.is_system.is_(False))
            .order_by(AssignmentORM.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars())

    async def assignment_counts(self, assignment_id: str) -> tuple[int, int, int]:
        questions = select(func.count()).where(QuestionORM.assignment_id == assignment_id)
        criteria = select(func.count()).where(CriterionORM.assignment_id == assignment_id)
        submissions = select(func.count()).where(SubmissionORM.assignment_id == assignment_id)
        row = (
            await self.db.execute(
                select(
                    questions.scalar_subquery(),
                    criteria.scalar_subquery(),
                    submissions.scalar_subquery(),
                )
            )
        ).one()
        return row[0], row[1], row[2]

    async def list_questions(self, assignment_id: str) -> list[QuestionORM]:
        stmt = (
            select(QuestionORM)
            .where(QuestionORM.assignment_id == assignment_id)
            .order_by(QuestionORM.position, QuestionORM.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def get_question(self, assignment_id: str, question_id: str) -> QuestionORM | None:
        stmt = select(QuestionORM).where(
            QuestionORM.assignment_id == assignment_id, QuestionORM.id == question_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def next_question_position(self, assignment_id: str) -> int:
        stmt = select(func.coalesce(func.max(QuestionORM.position), 0)).where(
            QuestionORM.assignment_id == assignment_id
        )
        return (await self.db.execute(stmt)).scalar_one() + 1

    async def list_criteria(self, assignment_id: str) -> list[CriterionORM]:
        stmt = (
            select(CriterionORM)
            .where(CriterionORM.assignment_id == assignment_id)
            .order_by(CriterionORM.position, CriterionORM.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def get_criterion(self, assignment_id: str, criterion_id: str) -> CriterionORM | None:
        stmt = select(CriterionORM).where(
            CriterionORM.assignment_id == assignment_id, CriterionORM.id == criterion_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def next_criterion_position(self, assignment_id: str) -> int:
        stmt = select(func.coalesce(func.max(CriterionORM.position), 0)).where(
            CriterionORM.assignment_id == assignment_id
        )
        return (await self.db.execute(stmt)).scalar_one() + 1

    async def submission_by_entry(self, entry_id: str) -> SubmissionORM | None:
        stmt = (
            select(SubmissionORM)
            .where(SubmissionORM.entry_id == entry_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def owned_submission(
        self, submission_id: str, teacher_id: str
    ) -> tuple[SubmissionORM, QueueSessionORM] | None:
        stmt = (
            select(SubmissionORM, QueueSessionORM)
            .join(QueueSessionORM, QueueSessionORM.id == SubmissionORM.session_id)
            .where(SubmissionORM.id == submission_id, QueueSessionORM.teacher_id == teacher_id)
        )
        row = (await self.db.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def lock_session_by_id(self, session_id: str) -> QueueSessionORM:
        stmt = (
            select(QueueSessionORM)
            .where(QueueSessionORM.id == session_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def refresh_submission(self, submission_id: str) -> SubmissionORM:
        stmt = (
            select(SubmissionORM)
            .where(SubmissionORM.id == submission_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def session_submission_count(self, session_id: str) -> int:
        stmt = select(func.count()).where(SubmissionORM.session_id == session_id)
        return (await self.db.execute(stmt)).scalar_one()

    async def list_session_submissions(self, session_id: str) -> Sequence[Row]:
        stmt = (
            select(SubmissionORM, EntryORM.ticket)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(SubmissionORM.session_id == session_id)
            .order_by(SubmissionORM.started_at)
        )
        return (await self.db.execute(stmt)).all()

    async def completed_for_analytics(
        self, assignment_id: str, session_id: str | None
    ) -> list[tuple[SubmissionORM, int, dict[str, int]]]:
        stmt = (
            select(SubmissionORM, EntryORM.ticket)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(
                SubmissionORM.assignment_id == assignment_id,
                SubmissionORM.status == SubmissionStatus.COMPLETED,
            )
            .order_by(SubmissionORM.started_at)
        )
        if session_id is not None:
            stmt = stmt.where(SubmissionORM.session_id == session_id)
        rows = (await self.db.execute(stmt)).all()
        ids = [row[0].id for row in rows]
        by_submission: dict[str, dict[str, int]] = {i: {} for i in ids}
        if ids:
            score_rows = await self.db.execute(
                select(ScoreORM.submission_id, ScoreORM.criterion_id, ScoreORM.points).where(
                    ScoreORM.submission_id.in_(ids)
                )
            )
            for submission_id, criterion_id, points in score_rows:
                by_submission[submission_id][criterion_id] = points
        return [(row[0], row[1], by_submission[row[0].id]) for row in rows]

    async def question_texts(self, submission_ids: list[str]) -> dict[str, list[str]]:
        result: dict[str, list[str]] = {i: [] for i in submission_ids}
        if not submission_ids:
            return result
        stmt = (
            select(SubmissionQuestionORM.submission_id, SubmissionQuestionORM.text)
            .where(SubmissionQuestionORM.submission_id.in_(submission_ids))
            .order_by(SubmissionQuestionORM.position)
        )
        for submission_id, text in await self.db.execute(stmt):
            result[submission_id].append(text)
        return result

    async def question_usage(self, student_id: str, assignment_id: str) -> dict[str, int]:
        stmt = (
            select(SubmissionQuestionORM.question_id, func.count())
            .join(SubmissionORM, SubmissionORM.id == SubmissionQuestionORM.submission_id)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(
                EntryORM.student_id == student_id,
                SubmissionORM.assignment_id == assignment_id,
                SubmissionORM.status != SubmissionStatus.CANCELLED,
                SubmissionQuestionORM.question_id.is_not(None),
            )
            .group_by(SubmissionQuestionORM.question_id)
        )
        return {qid: n for qid, n in (await self.db.execute(stmt)).all()}

    async def renumber_attempts(self, student_id: str, assignment_id: str) -> None:
        stmt = (
            select(SubmissionORM)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(
                EntryORM.student_id == student_id,
                SubmissionORM.assignment_id == assignment_id,
                SubmissionORM.status != SubmissionStatus.CANCELLED,
            )
            .order_by(SubmissionORM.started_at, SubmissionORM.created_at, SubmissionORM.id)
            .execution_options(populate_existing=True)
        )
        for number, submission in enumerate((await self.db.execute(stmt)).scalars(), start=1):
            submission.attempt_number = number

    async def current_submission(self, session_id: str) -> Row | None:
        stmt = (
            select(SubmissionORM, EntryORM.ticket)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(
                SubmissionORM.session_id == session_id,
                SubmissionORM.status == SubmissionStatus.IN_PROGRESS,
                EntryORM.status == "called",
            )
        )
        return (await self.db.execute(stmt)).first()

    async def submission_ticket(self, submission: SubmissionORM) -> int:
        stmt = select(EntryORM.ticket).where(EntryORM.id == submission.entry_id)
        return (await self.db.execute(stmt)).scalar_one()

    async def submission_questions(self, submission_id: str) -> list[SubmissionQuestionORM]:
        stmt = (
            select(SubmissionQuestionORM)
            .where(SubmissionQuestionORM.submission_id == submission_id)
            .order_by(SubmissionQuestionORM.position)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def scores(self, submission_id: str) -> Sequence[Row]:
        stmt = (
            select(ScoreORM, CriterionORM)
            .join(CriterionORM, CriterionORM.id == ScoreORM.criterion_id)
            .where(ScoreORM.submission_id == submission_id)
            .order_by(CriterionORM.position, CriterionORM.id)
        )
        return (await self.db.execute(stmt)).all()

    async def replace_scores(self, submission_id: str, rows: list[ScoreORM]) -> None:
        await self.db.execute(delete(ScoreORM).where(ScoreORM.submission_id == submission_id))
        self.db.add_all(rows)
        await self.db.flush()

    def add_audit(
        self,
        *,
        submission_id: str,
        actor_id: str,
        action: str,
        field: str,
        old_value: str | None,
        new_value: str | None,
        reason: str = "",
        occurred_at: datetime | None = None,
    ) -> None:
        entry = AuditLogORM(
            submission_id=submission_id,
            actor_id=actor_id,
            action=action,
            field=field,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )
        if occurred_at is not None:
            entry.occurred_at = occurred_at
        self.db.add(entry)

    async def audit_entries(self, submission_id: str) -> list[AuditLogORM]:
        stmt = (
            select(AuditLogORM)
            .where(AuditLogORM.submission_id == submission_id)
            .order_by(AuditLogORM.seq)
        )
        return list((await self.db.execute(stmt)).scalars())
