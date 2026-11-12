import asyncio
import secrets
import weakref
from datetime import UTC, datetime

from sqlalchemy import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.events import notify_changed
from core.exceptions import ConflictError, NotFoundError
from core.security import hash_token
from models.entry import EntryORM, EntryStatus
from models.protocol import AssignmentORM
from models.queue_session import QueueSessionORM, SessionStatus
from repositories.queue import QueueRepository
from schemas.queue import (
    JoinIn,
    MyEntryOut,
    NowServing,
    SessionCreate,
    SessionPublic,
    TeacherEntryOut,
    TeacherQueueOut,
)
from services.protocol_calls import (
    cancel_open_submission,
    cancel_open_submissions,
    has_open_submission,
    start_submission,
)
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
) -> MyEntryOut:
    position: int | None = None
    ahead: int | None = None
    eta: int | None = None
    if status == EntryStatus.WAITING:
        position = waiting_before + 1
        ahead = waiting_before + (1 if called_ticket is not None else 0)
        eta = ahead * avg_service_seconds
    elif status == EntryStatus.CALLED:
        position, ahead, eta = 0, 0, 0
    return MyEntryOut(
        ticket=ticket,
        name=name,
        status=status,
        position=position,
        people_ahead=ahead,
        eta_seconds=eta,
        session_status=session_status,
        now_serving=(
            NowServing(ticket=called_ticket, name=called_name or "")
            if called_ticket is not None
            else None
        ),
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
        for _ in range(10):
            try:
                async with self.db.begin_nested():
                    session = self.repo.add_session(
                        teacher_id,
                        data.title,
                        data.group,
                        generate_code(),
                        settings.DEFAULT_SERVICE_SECONDS,
                    )
                    await self.db.flush()
            except IntegrityError:
                continue
            await self.db.commit()
            return session
        raise ConflictError(
            "Could not allocate a session code, try again", code="code_allocation_failed"
        )

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
            now_serving=NowServing(ticket=called.ticket, name=called.name) if called else None,
            practice=practice,
        )

    async def join(self, code: str, data: JoinIn) -> MyEntryOut:
        async with join_gate(normalize_code(code)):
            return await self._join(code, data)

    async def _join(self, code: str, data: JoinIn) -> MyEntryOut:
        session = await self._get_session(code)
        token_hash = hash_token(data.token)
        existing = await self.repo.get_entry_by_token(session.id, token_hash)
        if existing is not None:
            return await self._view(existing.id)
        if session.status != SessionStatus.OPEN:
            raise ConflictError("Session is closed", code="session_closed")
        self._check_capacity(*await self.repo.queue_counts(session.id))
        try:
            ticket = await self.repo.allocate_ticket(session.id)
            inserted = await self.repo.insert_entry_if_room(
                session.id,
                data.name,
                token_hash,
                ticket,
                settings.MAX_QUEUE_SIZE,
                settings.MAX_ENTRIES_PER_SESSION,
            )
            if inserted is None:
                await self.db.rollback()
                self._check_capacity(*await self.repo.queue_counts(session.id))
                raise ConflictError("Queue is full", code="queue_full")
            entry_id = inserted
            await self.db.commit()
            notify_changed(session.code)
        except IntegrityError:
            await self.db.rollback()
            session = await self._get_session(code)
            existing = await self.repo.get_entry_by_token(session.id, token_hash)
            if existing is None:
                raise
            entry_id = existing.id
        return await self._view(entry_id)

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
        )

    async def close_session(self, code: str, teacher_id: str) -> QueueSessionORM:
        session = await self._owned(code, teacher_id, lock=True)
        if session.status != SessionStatus.CLOSED:
            await self.repo.close_session(session, datetime.now(UTC))
            await cancel_open_submissions(self.db, [session.id])
            await self.db.commit()
            notify_changed(session.code)
        return session

    async def call_next(self, code: str, teacher_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
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
        entry.status = EntryStatus.WAITING
        entry.called_at = None
        entry.finished_at = None
        await self.db.commit()
        notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

    async def promote(self, code: str, teacher_id: str, entry_id: str) -> TeacherEntryOut:
        session = await self._owned(code, teacher_id, lock=True)
        entry = await self._entry(session, entry_id)
        if entry.status != EntryStatus.WAITING:
            raise ConflictError("Only a waiting student can be moved forward", code="not_waiting")
        first = await self.repo.next_waiting(session.id)
        if first is not None and first.id != entry.id:
            entry.sort_order = first.sort_order - 1
            await self.db.commit()
            notify_changed(session.code)
        return TeacherEntryOut.model_validate(entry)

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
        )
