import asyncio
import random
import secrets
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import notify_changed
from core.exceptions import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    RateLimitError,
    UnprocessableError,
)
from core.security import hash_token
from models.entry import EntryORM, EntryStatus
from models.protocol import AssignmentORM, SessionEventORM
from models.queue_session import QueueSessionORM, SessionStatus
from repositories.queue import QueueRepository
from schemas.queue import (
    JoinIn,
    MyEntryOut,
    NowServing,
    PresenceIn,
    PresenceOut,
    SessionCreate,
    SessionPublic,
    SessionSettingsIn,
    TeacherEntryOut,
    TeacherQueueOut,
)
from schemas.roster import PublicRosterOut
from services import presence
from services.identity import find_or_create_student
from services.ordering import (
    ORDER_PARK,
    READY_BAND,
    TICKET_PARK,
    banded,
    lottery_plan,
    unbanded,
)
from services.protocol_calls import (
    cancel_open_submission,
    cancel_open_submissions,
    has_open_submission,
    restore_closed_submissions,
    start_submission,
)
from services.quick import QuickAssignments
from services.roster import RosterService
from services.serving import (  # noqa: F401
    AVERAGE_SMOOTHING,
    MAX_SERVICE_SECONDS,
    MIN_SERVICE_SECONDS,
    mark_entry_done,
)

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def normalize_code(code: str) -> str:
    return code.strip().upper()


def generate_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


@dataclass(frozen=True)
class QueueFlags:
    order_mode: str = "join"
    lottery_done: bool = False
    ready_priority: bool = False
    ready_bonus_points: int = 0
    ready_skip_questions: int = 0
    join_open: bool = True
    waiting_count: int = 0
    session_mode: str = "protocol"
    quick_max_points: int = 10
    assignment_id: str | None = None
    show_names: bool = False

    @property
    def in_pool(self) -> bool:
        return self.order_mode == "lottery" and not self.lottery_done

    @classmethod
    def of(cls, session: QueueSessionORM, waiting_count: int = 0) -> "QueueFlags":
        return cls(
            order_mode=session.order_mode,
            lottery_done=session.lottery_at is not None,
            ready_priority=session.ready_priority,
            ready_bonus_points=session.ready_bonus_points,
            ready_skip_questions=session.ready_skip_questions,
            join_open=session.join_open,
            waiting_count=waiting_count,
            session_mode=session.session_mode,
            quick_max_points=session.quick_max_points,
            assignment_id=session.assignment_id,
            show_names=session.show_student_names,
        )


def build_student_view(
    *,
    ticket: int,
    name: str,
    status: str,
    waiting_before: int,
    called_ticket: int | None,
    called_name: str | None,
    avg_service_seconds: int,
    session_status: str,
    not_ready: bool = False,
    not_ready_count: int = 0,
    ready_now: bool = False,
    flags: QueueFlags | None = None,
) -> MyEntryOut:
    flags = flags or QueueFlags()
    in_pool = flags.in_pool and status == EntryStatus.WAITING
    position: int | None = None
    ahead: int | None = None
    eta: int | None = None
    if in_pool:
        pass
    elif status == EntryStatus.WAITING:
        position = waiting_before + 1
        ahead = waiting_before + (1 if called_ticket is not None else 0)
        eta = ahead * avg_service_seconds
    elif status == EntryStatus.CALLED:
        position, ahead, eta = 0, 0, 0
    return MyEntryOut(
        ticket=None if in_pool else ticket,
        name=name,
        status=status,
        position=position,
        people_ahead=ahead,
        eta_seconds=eta,
        session_status=session_status,
        now_serving=(
            NowServing(ticket=called_ticket, name=(called_name or "") if flags.show_names else None)
            if called_ticket is not None
            else None
        ),
        not_ready=not_ready and status == EntryStatus.WAITING,
        not_ready_count=not_ready_count,
        not_ready_left=max(0, settings.NOT_READY_MAX_PER_SESSION - not_ready_count),
        ready_now=ready_now and status == EntryStatus.WAITING,
        in_pool=in_pool,
        pool_count=flags.waiting_count,
        join_open=flags.join_open,
        order_mode=flags.order_mode,
        ready_priority=flags.ready_priority,
        ready_bonus_points=flags.ready_bonus_points,
        ready_skip_questions=flags.ready_skip_questions,
    )


_join_gates: weakref.WeakValueDictionary[str, asyncio.Lock] = weakref.WeakValueDictionary()


def join_gate(code: str) -> asyncio.Lock:
    gate = _join_gates.get(code)
    if gate is None:
        gate = asyncio.Lock()
        _join_gates[code] = gate
    return gate


class QueueService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = QueueRepository(db)

    async def create_session(self, teacher_id: str, data: SessionCreate) -> QueueSessionORM:
        deadline = self._deadline(data)
        if data.roster_only:
            await self._require_roster(teacher_id, data.group)
        assignment_id = None
        if data.session_mode == "quick":
            assignment_id = await QuickAssignments(self.db).ensure(
                teacher_id, data.quick_max_points
            )
        for _ in range(10):
            try:
                async with self.db.begin_nested():
                    session = self.repo.add_session(
                        teacher_id,
                        data.title,
                        data.group,
                        generate_code(),
                        settings.DEFAULT_SERVICE_SECONDS,
                        order_mode=data.order_mode,
                        ready_priority=data.ready_priority,
                        ready_bonus_points=data.ready_bonus_points,
                        ready_skip_questions=data.ready_skip_questions,
                        deadline_at=deadline,
                        session_mode=data.session_mode,
                        presence_check=data.presence_check,
                        presence_interval=data.presence_interval,
                        quick_max_points=data.quick_max_points,
                        assignment_id=assignment_id,
                        roster_only=data.roster_only,
                        show_student_names=data.show_student_names,
                    )
                    await self.db.flush()
            except IntegrityError:
                continue
            await self.db.commit()
            return session
        raise ConflictError(
            "Could not allocate a session code, try again", code="code_allocation_failed"
        )

    @staticmethod
    def _deadline(data: SessionCreate) -> datetime | None:
        now = datetime.now(UTC)
        if data.duration_minutes is not None:
            return now + timedelta(minutes=data.duration_minutes)
        if data.deadline_at is None:
            return None
        limit = timedelta(minutes=settings.BUDGET_MAX_DURATION_MINUTES)
        if data.deadline_at <= now + timedelta(minutes=1):
            raise UnprocessableError(
                "Время окончания должно быть позже текущего", code="invalid_deadline"
            )
        if data.deadline_at > now + limit:
            raise UnprocessableError(
                "Время окончания слишком далеко: не больше "
                f"{settings.BUDGET_MAX_DURATION_MINUTES // 60} ч от текущего",
                code="invalid_deadline",
            )
        return data.deadline_at

    async def list_sessions(self, teacher_id: str) -> list[QueueSessionORM]:
        return await self.repo.list_sessions(teacher_id)

    async def public_info(self, code: str) -> SessionPublic:
        session = await self._get_session(code)
        called = await self.repo.get_called(session.id)
        practice = False
        if session.assignment_id is not None:
            assignment = await self.db.get(AssignmentORM, session.assignment_id)
            practice = bool(assignment and assignment.practice_enabled)
        return SessionPublic(
            code=session.code,
            title=session.title,
            group=session.group_name,
            status=session.status,
            waiting_count=await self.repo.count_waiting(session.id),
            now_serving=NowServing(
                ticket=called.ticket, name=called.name if session.show_student_names else None
            )
            if called
            else None,
            practice=practice,
            join_open=session.join_open,
            order_mode=session.order_mode,
            ready_priority=session.ready_priority,
            ready_bonus_points=session.ready_bonus_points,
            ready_skip_questions=session.ready_skip_questions,
            lottery_done=session.lottery_at is not None,
            session_mode=session.session_mode,
            presence_check=session.presence_check,
            graded=session.assignment_id is not None,
            roster_only=session.roster_only,
            roster_available=await RosterService(self.db).has_roster(
                session.teacher_id, session.group_name
            ),
        )

    async def join(self, code: str, data: JoinIn, client_ip: str = "-") -> MyEntryOut:
        async with join_gate(normalize_code(code)):
            return await self._join(code, data, client_ip)

    async def _roster_name(self, session: QueueSessionORM, name: str) -> str:
        canonical = await RosterService(self.db).canonical(
            session.teacher_id, session.group_name, name
        )
        if canonical is None:
            raise UnprocessableError("Вас нет в списке группы", code="not_in_roster")
        if await self.repo.name_in_queue(session.id, canonical):
            raise ConflictError("Этот студент уже в очереди", code="name_taken")
        return canonical

    def _check_presence(self, session: QueueSessionORM, code: str | None, client_ip: str) -> None:
        wait = presence.guard.retry_after(session.code, client_ip)
        if wait:
            raise RateLimitError(
                f"Слишком много неверных кодов. Подождите {wait} с и посмотрите на экран",
                headers={"Retry-After": str(wait)},
                code="presence_locked",
            )
        if not code or not code.strip():
            raise ForbiddenError(
                "Введите код с экрана преподавателя", code="presence_code_required"
            )
        digits = "".join(code.split()).replace("-", "")
        if not presence.is_valid(
            settings.JWT_SECRET_KEY,
            session.id,
            session.presence_interval,
            presence.wall_clock(),
            digits,
        ):
            presence.guard.failed(session.code, client_ip)
            raise ForbiddenError("Неверный код, посмотрите на экран", code="presence_code_invalid")

    async def _join(self, code: str, data: JoinIn, client_ip: str = "-") -> MyEntryOut:
        session = await self._lock(code)
        token_hash = hash_token(data.token)
        existing = await self.repo.get_entry_by_token(session.id, token_hash)
        if existing is not None:
            entry_id = existing.id
            await self.db.rollback()
            return await self._view(entry_id)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if session.presence_check == "rotating_code":
            self._check_presence(session, data.presence_code, client_ip)
        self._check_registration(session)
        session_id, session_code = session.id, session.code
        name = data.name
        if session.roster_only:
            name = await self._roster_name(session, data.name)
        try:
            student = await find_or_create_student(
                self.db, session.teacher_id, session.group_name, name
            )
            ticket = await self.repo.allocate_ticket(session.id)
            wants_ready = data.ready_now and session.ready_priority and session.lottery_at is None
            inserted = await self.repo.insert_entry_if_room(
                session.id,
                name,
                token_hash,
                ticket,
                settings.MAX_QUEUE_SIZE,
                settings.MAX_ENTRIES_PER_SESSION,
                student.id,
                hash_token(data.device_key) if data.device_key else None,
                ticket - READY_BAND if wants_ready else ticket,
                wants_ready,
            )
            if inserted is None:
                await self.db.rollback()
                self._check_capacity(*await self.repo.queue_counts(session_id))
                raise ConflictError("Queue is full", code="queue_full")
            entry_id = inserted
            await self.db.commit()
            notify_changed(session_code)
        except IntegrityError:
            await self.db.rollback()
            existing = await self.repo.get_entry_by_token(session_id, token_hash)
            if existing is None:
                raise
            entry_id = existing.id
        return await self._view(entry_id)

    @staticmethod
    def _check_registration(session: QueueSessionORM) -> None:
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if not session.join_open:
            raise ConflictError("Запись закрыта", code="registration_closed")

    def _check_capacity(self, waiting: int, total: int) -> None:
        if waiting >= settings.MAX_QUEUE_SIZE:
            raise ConflictError("Queue is full", code="queue_full")
        if total >= settings.MAX_ENTRIES_PER_SESSION:
            raise ConflictError("Session is full", code="session_full")

    async def my_status(self, code: str, token: str) -> MyEntryOut:
        row = await self.repo.student_view_by_token(normalize_code(code), hash_token(token))
        if row is None:
            await self._get_session(code)
            raise NotFoundError("Entry not found", code="entry_not_found")
        return self._to_student_view(row)

    async def live_entry_id(self, code: str, token: str) -> tuple[str, str]:
        session = await self._get_session(code)
        entry = await self.repo.get_entry_by_token(session.id, hash_token(token))
        if entry is None:
            raise NotFoundError("Entry not found", code="entry_not_found")
        return session.code, entry.id

    async def live_teacher_code(self, code: str, teacher_id: str) -> str:
        return (await self._owned(code, teacher_id)).code

    async def leave(self, code: str, token: str) -> MyEntryOut:
        session = await self._get_session(code)
        entry = await self.repo.get_entry_by_token(session.id, hash_token(token))
        if entry is None:
            raise NotFoundError("Entry not found", code="entry_not_found")
        await self._lock(session.code)
        entry = await self.repo.get_entry(session.id, entry.id) or entry
        if entry.status in (EntryStatus.WAITING, EntryStatus.CALLED):
            entry.status = EntryStatus.LEFT
            entry.finished_at = datetime.now(UTC)
            await cancel_open_submission(self.db, session, entry)
            await self.db.commit()
            notify_changed(session.code)
        return await self._view(entry.id)

    async def teacher_queue(self, code: str, teacher_id: str) -> TeacherQueueOut:
        session = await self._owned(code, teacher_id)
        entries = await self.repo.list_open_entries(session.id)
        return TeacherQueueOut(
            code=session.code,
            title=session.title,
            group=session.group_name,
            status=session.status,
            avg_service_seconds=session.avg_service_seconds,
            waiting_count=sum(1 for e in entries if e.status == EntryStatus.WAITING),
            entries=[TeacherEntryOut.model_validate(e) for e in entries],
            join_open=session.join_open,
            not_ready_max=settings.NOT_READY_MAX_PER_SESSION,
            show_student_names=session.show_student_names,
            order_mode=session.order_mode,
            ready_priority=session.ready_priority,
            ready_bonus_points=session.ready_bonus_points,
            ready_skip_questions=session.ready_skip_questions,
            lottery_done=session.lottery_at is not None,
            session_mode=session.session_mode,
            quick_max_points=session.quick_max_points,
            assignment_id=session.assignment_id,
        )

    async def _locked_entry(self, code: str, token: str) -> tuple[QueueSessionORM, EntryORM]:
        session = await self._get_session(code)
        entry = await self.repo.get_entry_by_token(session.id, hash_token(token))
        if entry is None:
            raise NotFoundError("Запись не найдена", code="entry_not_found")
        session = await self._lock(session.code)
        entry = await self.repo.get_entry(session.id, entry.id) or entry
        return session, entry

    async def mark_not_ready(self, code: str, token: str) -> MyEntryOut:
        session, entry = await self._locked_entry(code, token)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if entry.status != EntryStatus.WAITING:
            raise ConflictError(
                "Отметить «не готов» можно, только пока вы ждёте в очереди", code="not_waiting"
            )
        if not entry.not_ready:
            if entry.not_ready_count >= settings.NOT_READY_MAX_PER_SESSION:
                raise ConflictError(
                    "Лимит отметок «не готов» в этой сессии исчерпан", code="not_ready_limit"
                )
            entry.not_ready = True
            entry.not_ready_count += 1
            await self.db.commit()
            notify_changed(session.code)
        return await self._view(entry.id)

    async def mark_ready(self, code: str, token: str) -> MyEntryOut:
        session, entry = await self._locked_entry(code, token)
        if entry.status != EntryStatus.WAITING:
            raise ConflictError(
                "Отметить «готов» можно, только пока вы ждёте в очереди", code="not_waiting"
            )
        if entry.not_ready:
            entry.not_ready = False
            await self.db.commit()
            notify_changed(session.code)
        return await self._view(entry.id)

    async def set_presence(self, code: str, teacher_id: str, data: PresenceIn) -> PresenceOut:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        session.presence_check = data.mode
        if data.interval is not None:
            session.presence_interval = data.interval
        await self.db.commit()
        return self._presence_view(session)

    async def get_presence(self, code: str, teacher_id: str) -> PresenceOut:
        return self._presence_view(await self._owned(code, teacher_id))

    @staticmethod
    def _presence_view(session: QueueSessionORM) -> PresenceOut:
        if session.presence_check != "rotating_code":
            return PresenceOut(enabled=False, interval=session.presence_interval)
        moment = presence.wall_clock()
        return PresenceOut(
            enabled=True,
            interval=session.presence_interval,
            code=presence.current_code(
                settings.JWT_SECRET_KEY, session.id, session.presence_interval, moment
            ),
            expires_in=presence.seconds_left(session.presence_interval, moment),
        )

    async def set_registration(self, code: str, teacher_id: str, is_open: bool) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if is_open and session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if session.join_open != is_open:
            session.join_open = is_open
            await self.db.commit()
            notify_changed(session.code)
        return session

    async def close_session(self, code: str, teacher_id: str) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.CLOSED:
            await self.repo.close_session(session, datetime.now(UTC))
            cancelled = await cancel_open_submissions(self.db, [session.id])
            self.db.add(
                SessionEventORM(
                    session_id=session.id,
                    actor_id=teacher_id,
                    kind="close",
                    details={"cancelled_submissions": cancelled},
                )
            )
            await self.db.commit()
            notify_changed(session.code)
        return session

    async def reopen_session(self, code: str, teacher_id: str) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.CLOSED:
            return session
        window = settings.SESSION_REOPEN_HOURS
        closed_at = session.closed_at
        if window <= 0 or (
            closed_at is not None and datetime.now(UTC) - closed_at > timedelta(hours=window)
        ):
            raise ConflictError(
                f"Сессию можно открыть снова только в течение {window} ч после закрытия"
                if window > 0
                else "Повторное открытие отключено",
                code="reopen_expired",
            )
        session.status = SessionStatus.OPEN
        session.closed_at = None
        restored = await restore_closed_submissions(self.db, session.id)
        self.db.add(
            SessionEventORM(
                session_id=session.id,
                actor_id=teacher_id,
                kind="reopen",
                details={"restored_submissions": restored},
            )
        )
        await self.db.commit()
        notify_changed(session.code)
        return session

    async def call_next(self, code: str, teacher_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        self._check_order_defined(session)
        if await self.repo.get_called(session.id) is not None:
            raise ConflictError("Finish the current student first", code="already_serving")
        entry = await self.repo.next_waiting(session.id)
        if entry is None:
            raise ConflictError("Queue is empty", code="queue_empty")
        entry.status = EntryStatus.CALLED
        entry.called_at = datetime.now(UTC)
        await start_submission(self.db, session, entry)
        await self.db.commit()
        notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    async def finish(self, code: str, teacher_id: str, entry_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        entry = await self._entry(session, entry_id)
        if entry.status == EntryStatus.DONE:
            return TeacherEntryOut.model_validate(entry)
        if entry.status != EntryStatus.CALLED:
            raise ConflictError("Only a called student can be marked as done", code="not_called")
        if await has_open_submission(self.db, session, entry):
            raise ConflictError("Complete the submission with grades first", code="submission_open")
        await mark_entry_done(self.repo, session, entry)
        await self.db.commit()
        notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    async def skip(self, code: str, teacher_id: str, entry_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        entry = await self._entry(session, entry_id)
        if entry.status == EntryStatus.SKIPPED:
            return TeacherEntryOut.model_validate(entry)
        if entry.status not in (EntryStatus.WAITING, EntryStatus.CALLED):
            raise ConflictError("This entry can no longer be skipped", code="cannot_skip")
        entry.status = EntryStatus.SKIPPED
        entry.finished_at = datetime.now(UTC)
        await cancel_open_submission(self.db, session, entry)
        await self.db.commit()
        notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    async def requeue(self, code: str, teacher_id: str, entry_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        entry = await self._entry(session, entry_id)
        if entry.status == EntryStatus.WAITING:
            return TeacherEntryOut.model_validate(entry)
        if entry.status != EntryStatus.SKIPPED:
            raise ConflictError(
                "Only a skipped student can be returned to the queue", code="not_skipped"
            )
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Session is closed", code="session_closed")
        entry.sort_order = await self.repo.allocate_ticket(session.id)
        entry.not_ready = False
        entry.status = EntryStatus.WAITING
        entry.called_at = None
        entry.finished_at = None
        await self.db.commit()
        notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    async def promote(self, code: str, teacher_id: str, entry_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        self._check_order_defined(session)
        entry = await self._entry(session, entry_id)
        if entry.status != EntryStatus.WAITING:
            raise ConflictError("Only a waiting student can be moved forward", code="not_waiting")
        first = await self.repo.next_waiting(session.id)
        if first is not None and first.id != entry.id:
            lowest = await self.repo.min_waiting_sort_order(session.id)
            entry.sort_order = (lowest if lowest is not None else entry.sort_order) - 1
            entry.not_ready = False
            await self.db.commit()
            notify_changed(session.code)
        elif entry.not_ready:
            entry.not_ready = False
            await self.db.commit()
            notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    @staticmethod
    def _check_order_defined(session: QueueSessionORM) -> None:
        if session.order_mode == "lottery" and session.lottery_at is None:
            raise ConflictError(
                "Порядок ещё не определён: закройте запись и перемешайте очередь",
                code="lottery_pending",
            )

    async def set_ready_now(self, code: str, token: str, value: bool) -> MyEntryOut:
        session, entry = await self._locked_entry(code, token)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if not session.ready_priority:
            raise ConflictError(
                "В этой сессии приоритет по готовности не включён", code="ready_priority_off"
            )
        if entry.status != EntryStatus.WAITING:
            raise ConflictError(
                "Изменить готовность можно, только пока вы ждёте в очереди", code="not_waiting"
            )
        if not session.join_open or session.lottery_at is not None:
            raise ConflictError(
                "Запись закрыта, готовность изменить уже нельзя", code="ready_locked"
            )
        if entry.ready_now != value:
            entry.ready_now = value
            entry.sort_order = banded(entry.sort_order) if value else unbanded(entry.sort_order)
            await self.db.commit()
            notify_changed(session.code)
        return await self._view(entry.id)

    async def public_roster(self, code: str) -> PublicRosterOut:
        session = await self._get_session(code)
        return await RosterService(self.db).public(
            session.id, session.teacher_id, session.group_name, session.roster_only
        )

    async def _require_roster(self, teacher_id: str, group: str | None) -> None:
        if not await RosterService(self.db).has_roster(teacher_id, group):
            raise UnprocessableError(
                "Для входа по списку загрузите список этой группы", code="roster_missing"
            )

    async def update_settings(
        self, code: str, teacher_id: str, data: SessionSettingsIn
    ) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        changes = data.model_dump(exclude_unset=True, exclude_none=True)
        names = changes.pop("show_student_names", None)
        if names is not None:
            session.show_student_names = names
        if changes and (not session.join_open or session.lottery_at is not None):
            raise ConflictError(
                "Настройки порядка можно менять только до закрытия записи", code="settings_locked"
            )
        if changes.get("roster_only"):
            await self._require_roster(teacher_id, session.group_name)
        priority_before = session.ready_priority
        for name, value in changes.items():
            setattr(session, name, value)
        await self.db.flush()
        if session.ready_priority != priority_before:
            await self.repo.shift_ready_band(session.id, READY_BAND, on=session.ready_priority)
        await self.db.commit()
        notify_changed(session.code)
        return session

    async def run_lottery(
        self, code: str, teacher_id: str, rng: random.Random | None = None
    ) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if session.order_mode != "lottery":
            raise ConflictError("Жеребьёвка в этой сессии не включена", code="lottery_off")
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Сессия закрыта", code="session_closed")
        if session.lottery_at is not None:
            return session
        entries = await self.repo.session_entries(session.id)
        waiting = [e for e in entries if e.status == EntryStatus.WAITING]
        rest = [e.id for e in entries if e.status != EntryStatus.WAITING]
        ready_ids = {e.id for e in waiting if e.ready_now} if session.ready_priority else set()
        if session.ready_priority:
            groups = [
                [e.id for e in waiting if e.id in ready_ids],
                [e.id for e in waiting if e.id not in ready_ids],
            ]
            labels = ["ready", "rest"]
        else:
            groups = [[e.id for e in waiting]]
            labels = ["all"]
        drawn, numbers = lottery_plan(groups, rest, rng)
        final = {
            entry_id: (number, number - READY_BAND if entry_id in ready_ids else number)
            for entry_id, number in numbers.items()
        }
        await self.repo.apply_numbers(session.id, final, TICKET_PARK, ORDER_PARK)
        first = 1
        for label, order in zip(labels, drawn, strict=True):
            self.db.add(
                SessionEventORM(
                    session_id=session.id,
                    actor_id=teacher_id,
                    kind="lottery",
                    details={
                        "group": label,
                        "participants": len(order),
                        "first_ticket": first,
                        "order": order,
                    },
                )
            )
            first += len(order)
        session.lottery_at = datetime.now(UTC)
        session.join_open = False
        session.next_ticket = len(entries) + 1
        await self.db.commit()
        notify_changed(session.code)
        return session

    async def session_events(self, code: str, teacher_id: str) -> list[SessionEventORM]:
        session = await self._owned(code, teacher_id)
        return await self.repo.list_events(session.id)

    async def _get_session(self, code: str) -> QueueSessionORM:
        session = await self.repo.get_session(normalize_code(code))
        if session is None:
            raise NotFoundError("Session not found", code="session_not_found")
        return session

    async def _lock(self, code: str) -> QueueSessionORM:
        session = await self.repo.get_session(normalize_code(code), lock=True)
        if session is None:
            raise NotFoundError("Session not found", code="session_not_found")
        return session

    async def _owned(self, code: str, teacher_id: str, *, lock: bool = False) -> QueueSessionORM:
        session = await self.repo.get_session(normalize_code(code), lock=lock)
        if session is None or session.teacher_id != teacher_id:
            raise NotFoundError("Session not found", code="session_not_found")
        return session

    async def _entry(self, session: QueueSessionORM, entry_id: str) -> EntryORM:
        entry = await self.repo.get_entry(session.id, entry_id)
        if entry is None:
            raise NotFoundError("Entry not found", code="entry_not_found")
        return entry

    async def _view(self, entry_id: str) -> MyEntryOut:
        row = await self.repo.student_view_by_entry(entry_id)
        if row is None:
            raise NotFoundError("Entry not found", code="entry_not_found")
        return self._to_student_view(row)

    def _to_student_view(self, row: Row) -> MyEntryOut:
        return build_student_view(
            ticket=row.ticket,
            name=row.name,
            status=row.status,
            waiting_before=row.waiting_before,
            called_ticket=row.called_ticket,
            called_name=row.called_name,
            avg_service_seconds=row.avg_service_seconds,
            session_status=row.session_status,
            not_ready=row.not_ready,
            not_ready_count=row.not_ready_count,
            ready_now=row.ready_now,
            flags=QueueFlags(
                order_mode=row.order_mode,
                lottery_done=row.lottery_at is not None,
                ready_priority=row.ready_priority,
                ready_bonus_points=row.ready_bonus_points,
                ready_skip_questions=row.ready_skip_questions,
                join_open=row.join_open,
                waiting_count=row.waiting_total,
                show_names=row.show_student_names,
            ),
        )
