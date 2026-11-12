from collections import Counter

from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import ConflictError, NotFoundError
from core.security import hash_token
from models.protocol import SubmissionStatus
from models.student import StudentORM
from repositories.history import HistoryRepository
from repositories.protocol import ProtocolRepository
from repositories.queue import QueueRepository
from schemas.history import (
    AssignmentHistoryOut,
    AttemptOut,
    HistoryQuestionOut,
    HistoryScoreOut,
    MyAttemptOut,
    MyHistoryOut,
    SeenQuestionOut,
    StudentCardOut,
    StudentListItem,
)
from services.identity import group_key, name_key
from services.queue import normalize_code


def percent_of(total: int | None, maximum: int | None) -> float | None:
    if total is None or not maximum:
        return None
    return round(100 * total / maximum, 1)


def add_deltas(percents: list[float | None]) -> list[float | None]:
    deltas: list[float | None] = []
    previous: float | None = None
    for value in percents:
        if value is None:
            deltas.append(None)
            continue
        deltas.append(round(value - previous, 1) if previous is not None else None)
        previous = value
    return deltas


class HistoryService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = HistoryRepository(db)
        self.queue_repo = QueueRepository(db)
        self.protocol_repo = ProtocolRepository(db)

    async def _student(self, student_id: str, teacher_id: str, *, lock: bool = False) -> StudentORM:
        student = await self.repo.get_student(student_id, teacher_id, lock=lock)
        if student is None:
            raise NotFoundError("Студент не найден", code="student_not_found")
        return student

    async def list_students(
        self,
        teacher_id: str,
        group: str | None,
        assignment_id: str | None,
        query: str | None,
    ) -> list[StudentListItem]:
        if assignment_id is not None and (
            await self.protocol_repo.get_assignment(assignment_id, teacher_id) is None
        ):
            raise NotFoundError("Работа не найдена", code="assignment_not_found")
        rows = await self.repo.list_students(
            teacher_id,
            group_key(group) if group is not None else None,
            assignment_id,
            name_key(query) if query else None,
        )
        return [
            StudentListItem(
                id=r.id,
                name=r.display_name,
                group=r.group_name,
                submissions=r.submissions,
                last_started_at=r.last_started_at,
            )
            for r in rows
        ]

    async def _attempt_outs(self, rows: list) -> dict[str, list[tuple[AttemptOut, str]]]:
        ids = [row[0].id for row in rows]
        scores = await self.repo.scores_for(ids)
        questions = await self.repo.questions_for(ids)
        grouped: dict[str, list[tuple[AttemptOut, str]]] = {}
        for submission, session, assignment in rows:
            completed = submission.status == SubmissionStatus.COMPLETED
            out = AttemptOut(
                submission_id=submission.id,
                session_code=session.code,
                session_title=session.title,
                attempt_number=(
                    None
                    if submission.status == SubmissionStatus.CANCELLED
                    else submission.attempt_number
                ),
                status=submission.status,
                started_at=submission.started_at,
                ended_at=submission.ended_at,
                total_points=submission.total_points if completed else None,
                max_points=submission.max_points if completed else None,
                percent=(
                    percent_of(submission.total_points, submission.max_points)
                    if completed
                    else None
                ),
                delta_percent=None,
                comment=submission.comment if completed else "",
                scores=[
                    HistoryScoreOut(name=n, points=p, max_points=m)
                    for n, p, m in scores[submission.id]
                ],
                questions=[
                    HistoryQuestionOut(text=q.text, difficulty=q.difficulty, topic=q.topic)
                    for q in questions[submission.id]
                ],
            )
            grouped.setdefault(assignment.id, []).append((out, assignment.title))
        for items in grouped.values():
            deltas = add_deltas([out.percent for out, _ in items])
            for (out, _), delta in zip(items, deltas, strict=True):
                out.delta_percent = delta
        return grouped

    async def card(
        self, teacher_id: str, student_id: str, assignment_id: str | None
    ) -> StudentCardOut:
        student = await self._student(student_id, teacher_id)
        if assignment_id is not None and (
            await self.protocol_repo.get_assignment(assignment_id, teacher_id) is None
        ):
            raise NotFoundError("Работа не найдена", code="assignment_not_found")
        rows = await self.repo.attempts(
            teacher_id=teacher_id, student_id=student.id, assignment_id=assignment_id
        )
        grouped = await self._attempt_outs(rows)
        assignments = []
        for key, items in grouped.items():
            attempts = [out for out, _ in items]
            seen = Counter(
                q.text
                for a in attempts
                if a.status != SubmissionStatus.CANCELLED
                for q in a.questions
            )
            assignments.append(
                AssignmentHistoryOut(
                    assignment_id=key,
                    title=items[0][1],
                    attempts=attempts,
                    seen_questions=[
                        SeenQuestionOut(text=t, times=n) for t, n in seen.most_common()
                    ],
                )
            )
        return StudentCardOut(
            id=student.id,
            name=student.display_name,
            group=student.group_name,
            names=await self.repo.student_names(student.id),
            assignments=assignments,
        )

    async def merge(self, teacher_id: str, student_id: str, into_id: str) -> StudentCardOut:
        if student_id == into_id:
            raise ConflictError("Нельзя объединить студента с самим собой", code="merge_same")
        await self._student(student_id, teacher_id)
        await self._student(into_id, teacher_id)
        await self.repo.lock_assignments(await self.repo.assignment_ids_of([student_id, into_id]))
        await self.repo.lock_students([student_id, into_id])
        source = await self._student(student_id, teacher_id, lock=True)
        target = await self._student(into_id, teacher_id, lock=True)
        if source.merged_into_id is not None or target.merged_into_id is not None:
            await self.db.rollback()
            raise ConflictError(
                "Одна из записей уже объединена с другой", code="student_already_merged"
            )
        source.merged_into_id = target.id
        await self.repo.repoint_entries(source.id, target.id)
        await self.db.flush()
        for assignment_id in await self.repo.assignment_ids_of([target.id]):
            await self.protocol_repo.renumber_attempts(target.id, assignment_id)
        await self.db.commit()
        return await self.card(teacher_id, target.id, None)

    async def reassign(
        self, teacher_id: str, submission_id: str, student_id: str | None
    ) -> StudentCardOut:
        found = await self.repo.entry_of_submission(submission_id, teacher_id)
        if found is None:
            raise NotFoundError("Сдача не найдена", code="submission_not_found")
        entry, session, submission = found
        await self.repo.lock_assignments([submission.assignment_id])
        if student_id is not None:
            target = await self._student(student_id, teacher_id)
            if target.merged_into_id is not None:
                raise ConflictError(
                    "Эта запись уже объединена с другой, выберите основную",
                    code="student_already_merged",
                )
        else:
            target = StudentORM(
                teacher_id=teacher_id,
                group_key=group_key(session.group_name),
                name_key=name_key(entry.name),
                display_name=entry.name,
                group_name=session.group_name,
                auto_link=False,
            )
            self.db.add(target)
            await self.db.flush()
        old_id = entry.student_id
        target_id = target.id
        if old_id == target_id:
            await self.db.rollback()
            return await self.card(teacher_id, target_id, None)
        await self.repo.lock_students(sorted({target_id, *([old_id] if old_id else [])}))
        entry.student_id = target_id
        await self.db.flush()
        owners = {target_id}
        if old_id is not None:
            owners.add(old_id)
        for owner in owners:
            await self.protocol_repo.renumber_attempts(owner, submission.assignment_id)
        await self.db.commit()
        return await self.card(teacher_id, target_id, None)

    async def my_history(self, code: str, token: str) -> MyHistoryOut:
        session = await self.queue_repo.get_session(normalize_code(code))
        if session is None:
            raise NotFoundError("Сессия не найдена", code="session_not_found")
        entry = await self.queue_repo.get_entry_by_token(session.id, hash_token(token))
        if entry is None:
            raise NotFoundError("Запись не найдена", code="entry_not_found")
        if entry.student_id is not None and entry.device_hash is not None:
            rows = await self.repo.attempts(
                teacher_id=session.teacher_id,
                student_id=entry.student_id,
                device_hash=entry.device_hash,
                completed_only=True,
            )
        else:
            rows = await self.repo.attempts(
                teacher_id=session.teacher_id, entry_id=entry.id, completed_only=True
            )
        attempts = []
        for items in (await self._attempt_outs(rows)).values():
            for out, title in items:
                attempts.append(
                    MyAttemptOut(
                        assignment_title=title,
                        attempt_number=out.attempt_number or 1,
                        ended_at=out.ended_at,
                        total_points=out.total_points,
                        max_points=out.max_points,
                        percent=out.percent,
                        delta_percent=out.delta_percent,
                        comment=out.comment,
                        scores=out.scores,
                        questions=out.questions,
                    )
                )
        return MyHistoryOut(attempts=attempts)
