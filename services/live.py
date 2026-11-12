import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import settings
from core.events import add_listener
from core.exceptions import OverloadedError
from db.database import AsyncSessionLocal
from models.entry import EntryStatus
from repositories.queue import QueueRepository
from schemas.queue import MyEntryOut, TeacherEntryOut, TeacherQueueOut
from services.queue import build_student_view

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = (EntryStatus.DONE, EntryStatus.LEFT)


@dataclass(frozen=True)
class EntryInfo:
    id: str
    ticket: int
    name: str
    status: str
    joined_at: datetime
    called_at: datetime | None
    not_ready: bool = False
    not_ready_count: int = 0


@dataclass(frozen=True)
class Snapshot:
    code: str
    title: str
    group: str | None
    status: str
    avg_service_seconds: int
    entries: tuple[EntryInfo, ...]
    position: dict[str, int] = field(default_factory=dict)
    called: EntryInfo | None = None
    join_open: bool = True

    @classmethod
    def build(
        cls,
        code: str,
        title: str,
        group: str | None,
        status: str,
        avg_service_seconds: int,
        entries: list,
        join_open: bool = True,
    ) -> "Snapshot":
        infos = tuple(
            EntryInfo(
                e.id,
                e.ticket,
                e.name,
                e.status,
                e.joined_at,
                e.called_at,
                e.not_ready,
                e.not_ready_count,
            )
            for e in entries
        )
        waiting = [e for e in infos if e.status == EntryStatus.WAITING]
        called = next((e for e in infos if e.status == EntryStatus.CALLED), None)
        return cls(
            code=code,
            title=title,
            group=group,
            status=status,
            avg_service_seconds=avg_service_seconds,
            entries=infos,
            position={e.id: i + 1 for i, e in enumerate(waiting)},
            called=called,
            join_open=join_open,
        )

    def student_view(self, entry_id: str) -> MyEntryOut | None:
        entry = next((e for e in self.entries if e.id == entry_id), None)
        if entry is None:
            return None
        position = self.position.get(entry_id)
        return build_student_view(
            ticket=entry.ticket,
            name=entry.name,
            status=entry.status,
            waiting_before=(position - 1) if position is not None else 0,
            called_ticket=self.called.ticket if self.called else None,
            called_name=self.called.name if self.called else None,
            avg_service_seconds=self.avg_service_seconds,
            session_status=self.status,
            not_ready=entry.not_ready,
            not_ready_count=entry.not_ready_count,
        )

    def teacher_view(self) -> TeacherQueueOut:
        return TeacherQueueOut(
            code=self.code,
            title=self.title,
            group=self.group,
            status=self.status,
            avg_service_seconds=self.avg_service_seconds,
            waiting_count=len(self.position),
            entries=[
                TeacherEntryOut(
                    id=e.id,
                    ticket=e.ticket,
                    name=e.name,
                    status=e.status,
                    joined_at=e.joined_at,
                    called_at=e.called_at,
                    not_ready=e.not_ready,
                    not_ready_count=e.not_ready_count,
                )
                for e in self.entries
            ],
            join_open=self.join_open,
            not_ready_max=settings.NOT_READY_MAX_PER_SESSION,
        )


Loader = Callable[[str], Awaitable[Snapshot | None]]


class Subscriber:
    def __init__(self) -> None:
        self.wake = asyncio.Event()
        self.closed = False


@dataclass
class _SessionState:
    subscribers: set[Subscriber] = field(default_factory=set)
    snapshot: Snapshot | None = None
    dirty: bool = False
    task: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class Hub:
    def __init__(self, loader: Loader, *, coalesce_seconds: float, max_per_session: int):
        self._loader = loader
        self._coalesce = coalesce_seconds
        self._max = max_per_session
        self._states: dict[str, _SessionState] = {}
        self.refreshes = 0
        self.rejected = 0

    def count(self, code: str | None = None) -> int:
        if code is not None:
            state = self._states.get(code)
            return len(state.subscribers) if state else 0
        return sum(len(s.subscribers) for s in self._states.values())

    def snapshot(self, code: str) -> Snapshot | None:
        state = self._states.get(code)
        return state.snapshot if state else None

    def check_capacity(self, code: str) -> None:
        if self.count(code) >= self._max:
            self.rejected += 1
            raise OverloadedError(
                "Too many live connections for this session", code="too_many_streams"
            )

    async def subscribe(self, code: str) -> Subscriber:
        self.check_capacity(code)
        state = self._states.setdefault(code, _SessionState())
        subscriber = Subscriber()
        state.subscribers.add(subscriber)
        try:
            async with state.lock:
                if state.snapshot is None:
                    state.snapshot = await self._loader(code)
        except BaseException:
            self.unsubscribe(code, subscriber)
            raise
        return subscriber

    def unsubscribe(self, code: str, subscriber: Subscriber) -> None:
        state = self._states.get(code)
        if state is None:
            return
        state.subscribers.discard(subscriber)
        if not state.subscribers:
            if state.task is not None:
                state.task.cancel()
            del self._states[code]

    def publish(self, code: str) -> None:
        state = self._states.get(code)
        if state is None or not state.subscribers:
            return
        state.dirty = True
        if state.task is None or state.task.done():
            state.task = asyncio.get_running_loop().create_task(self._refresh_loop(code, state))

    async def _refresh_loop(self, code: str, state: _SessionState) -> None:
        while state.dirty:
            state.dirty = False
            delay = self._coalesce
            try:
                snapshot = await self._loader(code)
            except Exception:
                logger.exception("Could not refresh live snapshot for %s", code)
                state.dirty = True
                delay = max(delay, 1.0)
            else:
                self.refreshes += 1
                state.snapshot = snapshot
                for subscriber in list(state.subscribers):
                    subscriber.wake.set()
            await asyncio.sleep(delay)

    async def close(self) -> None:
        for state in list(self._states.values()):
            if state.task is not None:
                state.task.cancel()
            for subscriber in list(state.subscribers):
                subscriber.closed = True
                subscriber.wake.set()


def _sse(event: str, data: str) -> str:
    return f"event: {event}\ndata: {data}\n\n"


Rendered = tuple[str, bool]


async def _stream(
    hub: Hub,
    code: str,
    render: Callable[[Snapshot], Rendered | None],
    on_missing: Callable[[], Awaitable[Rendered | None]],
    heartbeat: float,
) -> AsyncIterator[str]:
    try:
        subscriber = await hub.subscribe(code)
    except OverloadedError:
        yield _sse("end", '{"reason": "too many live connections"}')
        return
    last: str | None = None
    try:
        while True:
            subscriber.wake.clear()
            snapshot = hub.snapshot(code)
            rendered = render(snapshot) if snapshot is not None else None
            if rendered is None:
                rendered = await on_missing()
                if rendered is not None:
                    yield _sse("entry", rendered[0])
                yield _sse("end", "{}")
                return
            payload, finished = rendered
            if payload != last:
                last = payload
                yield _sse("entry", payload)
                if finished:
                    yield _sse("end", "{}")
                    return
            try:
                await asyncio.wait_for(subscriber.wake.wait(), heartbeat)
            except TimeoutError:
                yield ": ping\n\n"
            if subscriber.closed:
                yield _sse("end", '{"reason": "server shutdown"}')
                return
    finally:
        hub.unsubscribe(code, subscriber)


class LiveGateway:
    def __init__(self) -> None:
        self.session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal

    async def load_snapshot(self, code: str) -> Snapshot | None:
        async with self.session_factory() as db:
            repo = QueueRepository(db)
            session = await repo.get_session(code)
            if session is None:
                return None
            entries = await repo.list_open_entries(session.id)
            return Snapshot.build(
                session.code,
                session.title,
                session.group_name,
                session.status,
                session.avg_service_seconds,
                entries,
                session.join_open,
            )

    async def student_view_by_entry(self, entry_id: str) -> MyEntryOut | None:
        async with self.session_factory() as db:
            service_row = await QueueRepository(db).student_view_by_entry(entry_id)
        if service_row is None:
            return None
        return build_student_view(
            ticket=service_row.ticket,
            name=service_row.name,
            status=service_row.status,
            waiting_before=service_row.waiting_before,
            called_ticket=service_row.called_ticket,
            called_name=service_row.called_name,
            avg_service_seconds=service_row.avg_service_seconds,
            session_status=service_row.session_status,
            not_ready=service_row.not_ready,
            not_ready_count=service_row.not_ready_count,
        )


gateway = LiveGateway()
hub = Hub(
    gateway.load_snapshot,
    coalesce_seconds=settings.SSE_COALESCE_SECONDS,
    max_per_session=settings.SSE_MAX_PER_SESSION,
)


def _student_rendered(view: MyEntryOut) -> Rendered:
    return view.model_dump_json(), view.status in TERMINAL_STATUSES


def student_stream(code: str, entry_id: str, heartbeat: float | None = None) -> AsyncIterator[str]:
    def render(snapshot: Snapshot) -> Rendered | None:
        view = snapshot.student_view(entry_id)
        return _student_rendered(view) if view is not None else None

    async def on_missing() -> Rendered | None:
        view = await gateway.student_view_by_entry(entry_id)
        return _student_rendered(view) if view is not None else None

    return _stream(hub, code, render, on_missing, heartbeat or settings.SSE_HEARTBEAT_SECONDS)


def teacher_stream(code: str, heartbeat: float | None = None) -> AsyncIterator[str]:
    async def on_missing() -> Rendered | None:
        return None

    return _stream(
        hub,
        code,
        lambda snapshot: (snapshot.teacher_view().model_dump_json(), False),
        on_missing,
        heartbeat or settings.SSE_HEARTBEAT_SECONDS,
    )


add_listener(hub.publish)
