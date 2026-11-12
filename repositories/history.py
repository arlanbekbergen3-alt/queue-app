from collections.abc import Sequence

from sqlalchemy import Row, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.entry import EntryORM
from models.protocol import (
    AssignmentORM,
    CriterionORM,
    ScoreORM,
    SubmissionORM,
    SubmissionQuestionORM,
)
from models.queue_session import QueueSessionORM
from models.student import StudentORM

STUDENT_LIMIT = 500


class HistoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_student(
        self, student_id: str, teacher_id: str, *, lock: bool = False
    ) -> StudentORM | None:
        stmt = select(StudentORM).where(
            StudentORM.id == student_id, StudentORM.teacher_id == teacher_id
        )
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def lock_students(self, student_ids: Sequence[str]) -> None:
        stmt = (
            select(StudentORM.id)
            .where(StudentORM.id.in_(student_ids))
            .order_by(StudentORM.id)
            .with_for_update()
        )
        await self.db.execute(stmt)

    async def list_students(
        self, teacher_id: str, group_key: str | None, assignment_id: str | None, query: str | None
    ) -> list[Row]:
        counted = (
            select(
                EntryORM.student_id.label("student_id"),
                func.count(SubmissionORM.id).label("submissions"),
                func.max(SubmissionORM.started_at).label("last_started_at"),
            )
            .select_from(SubmissionORM)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(SubmissionORM.status != "cancelled")
            .group_by(EntryORM.student_id)
        )
        if assignment_id is not None:
            counted = counted.where(SubmissionORM.assignment_id == assignment_id)
        sub = counted.subquery()
        stmt = (
            select(
                StudentORM.id,
                StudentORM.display_name,
                StudentORM.group_name,
                func.coalesce(sub.c.submissions, 0).label("submissions"),
                sub.c.last_started_at,
            )
            .outerjoin(sub, sub.c.student_id == StudentORM.id)
            .where(StudentORM.teacher_id == teacher_id, StudentORM.merged_into_id.is_(None))
            .order_by(StudentORM.group_name, StudentORM.display_name, StudentORM.id)
            .limit(STUDENT_LIMIT)
        )
        if group_key is not None:
            stmt = stmt.where(StudentORM.group_key == group_key)
        if assignment_id is not None:
            stmt = stmt.where(sub.c.submissions.is_not(None))
        if query:
            stmt = stmt.where(StudentORM.name_key.contains(query))
        return list((await self.db.execute(stmt)).all())

    async def student_names(self, student_id: str) -> list[str]:
        stmt = select(EntryORM.name).where(EntryORM.student_id == student_id).distinct()
        names = set((await self.db.execute(stmt)).scalars())
        merged = select(StudentORM.display_name).where(StudentORM.merged_into_id == student_id)
        names |= set((await self.db.execute(merged)).scalars())
        return sorted(names)

    async def attempts(
        self,
        *,
        teacher_id: str,
        student_id: str | None = None,
        entry_id: str | None = None,
        assignment_id: str | None = None,
        device_hash: str | None = None,
        completed_only: bool = False,
    ) -> list[Row]:
        stmt = (
            select(SubmissionORM, QueueSessionORM, AssignmentORM)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .join(QueueSessionORM, QueueSessionORM.id == SubmissionORM.session_id)
            .join(AssignmentORM, AssignmentORM.id == SubmissionORM.assignment_id)
            .where(AssignmentORM.teacher_id == teacher_id)
            .order_by(AssignmentORM.title, AssignmentORM.id, SubmissionORM.started_at)
        )
        if student_id is not None:
            stmt = stmt.where(EntryORM.student_id == student_id)
        if entry_id is not None:
            stmt = stmt.where(EntryORM.id == entry_id)
        if assignment_id is not None:
            stmt = stmt.where(SubmissionORM.assignment_id == assignment_id)
        if device_hash is not None:
            stmt = stmt.where(EntryORM.device_hash == device_hash)
        if completed_only:
            stmt = stmt.where(SubmissionORM.status == "completed")
        return list((await self.db.execute(stmt)).all())

    async def scores_for(
        self, submission_ids: Sequence[str]
    ) -> dict[str, list[tuple[str, int, int]]]:
        result: dict[str, list[tuple[str, int, int]]] = {i: [] for i in submission_ids}
        if not submission_ids:
            return result
        stmt = (
            select(
                ScoreORM.submission_id,
                CriterionORM.name,
                ScoreORM.points,
                CriterionORM.max_points,
            )
            .join(CriterionORM, CriterionORM.id == ScoreORM.criterion_id)
            .where(ScoreORM.submission_id.in_(submission_ids))
            .order_by(CriterionORM.position, CriterionORM.id)
        )
        for submission_id, name, points, maximum in await self.db.execute(stmt):
            result[submission_id].append((name, points, maximum))
        return result

    async def questions_for(
        self, submission_ids: Sequence[str]
    ) -> dict[str, list[SubmissionQuestionORM]]:
        result: dict[str, list[SubmissionQuestionORM]] = {i: [] for i in submission_ids}
        if not submission_ids:
            return result
        stmt = (
            select(SubmissionQuestionORM)
            .where(SubmissionQuestionORM.submission_id.in_(submission_ids))
            .order_by(SubmissionQuestionORM.position)
        )
        for question in (await self.db.execute(stmt)).scalars():
            result[question.submission_id].append(question)
        return result

    async def repoint_entries(self, source_id: str, target_id: str) -> None:
        entries = select(EntryORM).where(EntryORM.student_id == source_id)
        for entry in (await self.db.execute(entries)).scalars():
            entry.student_id = target_id
        children = select(StudentORM).where(StudentORM.merged_into_id == source_id)
        for child in (await self.db.execute(children)).scalars():
            child.merged_into_id = target_id

    async def assignment_ids_of(self, student_ids: Sequence[str]) -> list[str]:
        stmt = (
            select(SubmissionORM.assignment_id)
            .join(EntryORM, EntryORM.id == SubmissionORM.entry_id)
            .where(EntryORM.student_id.in_(student_ids))
            .distinct()
            .order_by(SubmissionORM.assignment_id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def lock_assignments(self, assignment_ids: Sequence[str]) -> None:
        if not assignment_ids:
            return
        stmt = (
            select(AssignmentORM.id)
            .where(AssignmentORM.id.in_(assignment_ids))
            .order_by(AssignmentORM.id)
            .with_for_update()
        )
        await self.db.execute(stmt)

    async def entry_of_submission(self, submission_id: str, teacher_id: str) -> Row | None:
        stmt = (
            select(EntryORM, QueueSessionORM, SubmissionORM)
            .join(SubmissionORM, SubmissionORM.entry_id == EntryORM.id)
            .join(QueueSessionORM, QueueSessionORM.id == SubmissionORM.session_id)
            .where(SubmissionORM.id == submission_id, QueueSessionORM.teacher_id == teacher_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).first()
