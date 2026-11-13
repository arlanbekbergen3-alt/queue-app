from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import notify_changed
from core.exceptions import ConflictError, NotFoundError
from models.protocol import AssignmentORM, SessionEventORM
from models.queue_session import QueueSessionORM, SessionStatus
from repositories.queue import QueueRepository
from schemas.budget import BudgetDecisionIn, BudgetOut, SuggestionOut
from services.queue import normalize_code


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Forecast:
    remaining_seconds: int
    remaining_students: int
    reserve_seconds: int
    budget_seconds: int | None
    work_seconds: int
    forecast_end: datetime
    status: str


def forecast(
    now: datetime,
    deadline: datetime,
    waiting: int,
    serving_elapsed: float | None,
    avg_service_seconds: int,
    reserve_percent: int,
) -> Forecast:
    remaining = max(0, int((deadline - now).total_seconds()))
    students = waiting + (1 if serving_elapsed is not None else 0)
    reserve = remaining * reserve_percent // 100
    budget = (remaining - reserve) // students if students > 0 else None
    work = waiting * avg_service_seconds
    if serving_elapsed is not None:
        work += max(0, round(avg_service_seconds - serving_elapsed))
    end = now + timedelta(seconds=work)
    if students == 0:
        status = "idle"
    elif remaining == 0 or end > deadline:
        status = "over"
    elif work <= remaining - reserve:
        status = "ok"
    else:
        status = "tight"
    return Forecast(remaining, students, reserve, budget, work, end, status)


def reduced_end(now: datetime, work_seconds: int, questions: int, target: int) -> datetime:
    return now + timedelta(seconds=round(work_seconds * target / questions))


class BudgetService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = QueueRepository(db)

    async def _owned(self, code: str, teacher_id: str, *, lock: bool = False) -> QueueSessionORM:
        session = await self.repo.get_session(normalize_code(code), lock=lock)
        if session is None or session.teacher_id != teacher_id:
            raise NotFoundError("Session not found", code="session_not_found")
        return session

    async def _questions(self, session: QueueSessionORM) -> int | None:
        if session.assignment_id is None:
            return None
        assignment = await self.db.get(AssignmentORM, session.assignment_id)
        if assignment is None:
            return None
        value = assignment.questions_per_submission
        return min(value, session.questions_override) if session.questions_override else value

    async def view(self, code: str, teacher_id: str) -> BudgetOut:
        session = await self._owned(code, teacher_id)
        return await self._build(session)

    async def _build(self, session: QueueSessionORM) -> BudgetOut:
        if session.deadline_at is None:
            return BudgetOut(enabled=False)
        now = utcnow()
        waiting = await self.repo.count_waiting(session.id)
        called = await self.repo.get_called(session.id)
        elapsed = None
        if called is not None:
            started = called.called_at or now
            elapsed = max(0.0, (now - started).total_seconds())
        calc = forecast(
            now,
            session.deadline_at,
            waiting,
            elapsed,
            session.avg_service_seconds,
            settings.BUDGET_RESERVE_PERCENT,
        )
        questions = await self._questions(session)
        suggestion = None
        if calc.status == "over" and questions is not None and questions > 1:
            target = questions - 1
            if session.budget_declined != target:
                suggestion = SuggestionOut(
                    questions_from=questions,
                    questions_to=target,
                    forecast_end_at=reduced_end(now, calc.work_seconds, questions, target),
                )
        return BudgetOut(
            enabled=True,
            status=calc.status,
            deadline_at=session.deadline_at,
            remaining_seconds=calc.remaining_seconds,
            remaining_students=calc.remaining_students,
            reserve_seconds=calc.reserve_seconds,
            budget_seconds=calc.budget_seconds,
            avg_service_seconds=session.avg_service_seconds,
            forecast_end_at=calc.forecast_end,
            questions=questions,
            suggestion=suggestion,
        )

    async def decide(self, code: str, teacher_id: str, data: BudgetDecisionIn) -> BudgetOut:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        current = await self._questions(session)
        if current is None or data.questions >= current:
            shown = "не задано" if current is None else str(current)
            raise ConflictError(
                f"Число вопросов можно только уменьшить: сейчас {shown}",
                code="questions_not_reducible",
            )
        before = await self._build(session)
        end = before.forecast_end_at
        details = {
            "from": current,
            "to": data.questions,
            "remaining_students": before.remaining_students,
            "forecast_end_at": end.isoformat() if end else None,
        }
        if data.accept:
            session.questions_override = data.questions
            session.budget_declined = None
            kind = "questions_reduced"
        else:
            session.budget_declined = data.questions
            kind = "questions_declined"
        self.db.add(
            SessionEventORM(session_id=session.id, actor_id=teacher_id, kind=kind, details=details)
        )
        await self.db.commit()
        notify_changed(session.code)
        return await self._build(session)
