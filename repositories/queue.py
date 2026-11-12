import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    DateTime,
    Integer,
    Row,
    String,
    and_,
    bindparam,
    case,
    func,
    insert,
    literal,
    or_,
    select,
    update,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from models.entry import EntryORM, EntryStatus
from models.queue_session import QueueSessionORM

_waiting_alias = aliased(EntryORM)
_called_alias = aliased(EntryORM)

_waiting_before = (
    select(func.count())
    .where(
        _waiting_alias.session_id == QueueSessionORM.id,
        _waiting_alias.status == EntryStatus.WAITING,
        or_(
            _waiting_alias.not_ready < EntryORM.not_ready,
            and_(
                _waiting_alias.not_ready == EntryORM.not_ready,
                _waiting_alias.sort_order < EntryORM.sort_order,
            ),
        ),
    )
    .correlate(QueueSessionORM, EntryORM)
    .scalar_subquery()
)

_STUDENT_VIEW = (
    select(
        EntryORM.id,
        EntryORM.ticket,
        EntryORM.name,
        EntryORM.status,
        EntryORM.not_ready,
        EntryORM.not_ready_count,
        QueueSessionORM.status.label("session_status"),
        QueueSessionORM.avg_service_seconds,
        _waiting_before.label("waiting_before"),
        _called_alias.ticket.label("called_ticket"),
        _called_alias.name.label("called_name"),
    )
    .select_from(QueueSessionORM)
    .join(EntryORM, EntryORM.session_id == QueueSessionORM.id)
    .outerjoin(
        _called_alias,
        and_(
            _called_alias.session_id == QueueSessionORM.id,
            _called_alias.status == EntryStatus.CALLED,
        ),
    )
)

STUDENT_VIEW_BY_TOKEN = _STUDENT_VIEW.where(
    QueueSessionORM.code == bindparam("code"), EntryORM.token_hash == bindparam("token_hash")
)
STUDENT_VIEW_BY_ENTRY = _STUDENT_VIEW.where(EntryORM.id == bindparam("entry_id"))


class QueueRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    def add_session(
        self,
        teacher_id: str,
        title: str,
        group_name: str | None,
        code: str,
        avg_service_seconds: int,
    ) -> QueueSessionORM:
        session = QueueSessionORM(
            teacher_id=teacher_id,
            title=title,
            group_name=group_name,
            code=code,
            avg_service_seconds=avg_service_seconds,
        )
        self.db.add(session)
        return session

    async def get_session(self, code: str, *, lock: bool = False) -> QueueSessionORM | None:
        stmt = select(QueueSessionORM).where(QueueSessionORM.code == code)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def list_sessions(self, teacher_id: str) -> list[QueueSessionORM]:
        stmt = (
            select(QueueSessionORM)
            .where(QueueSessionORM.teacher_id == teacher_id)
            .order_by(QueueSessionORM.created_at.desc())
        )
        return list((await self.db.execute(stmt)).scalars())

    async def allocate_ticket(self, session_id: str) -> int:
        stmt = (
            update(QueueSessionORM)
            .where(QueueSessionORM.id == session_id)
            .values(next_ticket=QueueSessionORM.next_ticket + 1)
            .returning(QueueSessionORM.next_ticket)
            .execution_options(synchronize_session=False)
        )
        return (await self.db.execute(stmt)).scalar_one() - 1

    def add_entry(self, session_id: str, name: str, token_hash: str, ticket: int) -> EntryORM:
        entry = EntryORM(
            session_id=session_id,
            name=name,
            token_hash=token_hash,
            ticket=ticket,
            sort_order=ticket,
        )
        self.db.add(entry)
        return entry

    async def insert_entry_if_room(
        self,
        session_id: str,
        name: str,
        token_hash: str,
        ticket: int,
        max_waiting: int,
        max_total: int,
    ) -> str | None:
        in_session = EntryORM.session_id == session_id
        waiting = (
            select(func.count()).where(in_session, EntryORM.status == EntryStatus.WAITING)
        ).scalar_subquery()
        total = select(func.count()).where(in_session).scalar_subquery()
        entry_id = str(uuid.uuid4())
        source = select(
            literal(entry_id, String),
            literal(session_id, String),
            literal(name, String),
            literal(token_hash, String),
            literal(ticket, Integer),
            literal(ticket, Integer),
            literal(str(EntryStatus.WAITING), String),
            literal(datetime.now(UTC), DateTime(timezone=True)),
        ).where(waiting < max_waiting, total < max_total)
        stmt = (
            insert(EntryORM)
            .from_select(
                [
                    "id",
                    "session_id",
                    "name",
                    "token_hash",
                    "ticket",
                    "sort_order",
                    "status",
                    "joined_at",
                ],
                source,
            )
            .returning(EntryORM.id)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_entry_by_token(self, session_id: str, token_hash: str) -> EntryORM | None:
        stmt = select(EntryORM).where(
            EntryORM.session_id == session_id, EntryORM.token_hash == token_hash
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_entry(self, session_id: str, entry_id: str) -> EntryORM | None:
        stmt = (
            select(EntryORM)
            .where(EntryORM.session_id == session_id, EntryORM.id == entry_id)
            .execution_options(populate_existing=True)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def get_called(self, session_id: str) -> EntryORM | None:
        stmt = select(EntryORM).where(
            EntryORM.session_id == session_id, EntryORM.status == EntryStatus.CALLED
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def next_waiting(self, session_id: str) -> EntryORM | None:
        stmt = (
            select(EntryORM)
            .where(EntryORM.session_id == session_id, EntryORM.status == EntryStatus.WAITING)
            .order_by(EntryORM.not_ready, EntryORM.sort_order)
            .limit(1)
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def queue_counts(self, session_id: str) -> tuple[int, int]:
        waiting = func.count().filter(EntryORM.status == EntryStatus.WAITING)
        stmt = select(waiting, func.count()).where(EntryORM.session_id == session_id)
        row = (await self.db.execute(stmt)).one()
        return row[0], row[1]

    async def count_waiting(self, session_id: str) -> int:
        stmt = select(func.count()).where(
            EntryORM.session_id == session_id, EntryORM.status == EntryStatus.WAITING
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_waiting_before(self, session_id: str, ticket: int) -> int:
        stmt = select(func.count()).where(
            EntryORM.session_id == session_id,
            EntryORM.status == EntryStatus.WAITING,
            EntryORM.ticket < ticket,
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def list_open_entries(self, session_id: str) -> list[EntryORM]:
        stmt = (
            select(EntryORM)
            .where(
                EntryORM.session_id == session_id,
                EntryORM.status.in_([EntryStatus.CALLED, EntryStatus.WAITING, EntryStatus.SKIPPED]),
            )
            .order_by(
                case(
                    (EntryORM.status == EntryStatus.CALLED, 0),
                    (EntryORM.status == EntryStatus.WAITING, 1),
                    else_=2,
                ),
                EntryORM.not_ready,
                EntryORM.sort_order,
            )
        )
        return list((await self.db.execute(stmt)).scalars())

    async def min_waiting_sort_order(self, session_id: str) -> int | None:
        stmt = select(func.min(EntryORM.sort_order)).where(
            EntryORM.session_id == session_id, EntryORM.status == EntryStatus.WAITING
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def set_average(self, session_id: str, value: int) -> None:
        stmt = (
            update(QueueSessionORM)
            .where(QueueSessionORM.id == session_id)
            .values(avg_service_seconds=value)
            .execution_options(synchronize_session=False)
        )
        await self.db.execute(stmt)

    async def close_idle_sessions(self, cutoff: datetime, now: datetime) -> list[str]:
        recent_activity = (
            select(EntryORM.id)
            .where(
                EntryORM.session_id == QueueSessionORM.id,
                or_(
                    EntryORM.joined_at > cutoff,
                    EntryORM.called_at > cutoff,
                    EntryORM.finished_at > cutoff,
                ),
            )
            .exists()
        )
        stmt = (
            update(QueueSessionORM)
            .where(
                QueueSessionORM.status == "open",
                QueueSessionORM.created_at < cutoff,
                ~recent_activity,
            )
            .values(status="closed", closed_at=now)
            .returning(QueueSessionORM.code)
            .execution_options(synchronize_session=False)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def close_session(self, session: QueueSessionORM, at: datetime) -> None:
        session.status = "closed"
        session.closed_at = at

    async def student_view_by_token(self, code: str, token_hash: str) -> Row | None:
        result = await self.db.execute(
            STUDENT_VIEW_BY_TOKEN, {"code": code, "token_hash": token_hash}
        )
        return result.first()

    async def student_view_by_entry(self, entry_id: str) -> Row | None:
        result = await self.db.execute(STUDENT_VIEW_BY_ENTRY, {"entry_id": entry_id})
        return result.first()
