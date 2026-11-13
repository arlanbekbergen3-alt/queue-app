import argparse
import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.config import settings
from core.metrics import metrics
from db.database import AsyncSessionLocal, engine
from models.entry import EntryORM
from models.queue_session import QueueSessionORM, SessionStatus
from models.student import StudentORM

logger = logging.getLogger(__name__)

BATCH_SIZE = 100


@dataclass(frozen=True)
class PurgeResult:
    sessions: int
    students: int


def _expired(cutoff: datetime):
    return (
        QueueSessionORM.status == SessionStatus.CLOSED,
        QueueSessionORM.closed_at.is_not(None),
        QueueSessionORM.closed_at < cutoff,
    )


def _orphans(cutoff: datetime):
    has_entries = select(EntryORM.id).where(EntryORM.student_id == StudentORM.id).exists()
    return (~has_entries, StudentORM.created_at < cutoff)


async def purge_closed_sessions(
    factory: async_sessionmaker[AsyncSession],
    days: int,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    batch_size: int = BATCH_SIZE,
) -> PurgeResult:
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    async with factory() as db:
        if dry_run:
            sessions = (
                await db.execute(
                    select(func.count()).select_from(QueueSessionORM).where(*_expired(cutoff))
                )
            ).scalar_one()
            students = (
                await db.execute(
                    select(func.count()).select_from(StudentORM).where(*_orphans(cutoff))
                )
            ).scalar_one()
            return PurgeResult(sessions, students)
        sessions = 0
        while True:
            ids = list(
                (
                    await db.execute(
                        select(QueueSessionORM.id)
                        .where(*_expired(cutoff))
                        .order_by(QueueSessionORM.closed_at)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not ids:
                break
            deleted = await db.execute(
                delete(QueueSessionORM)
                .where(QueueSessionORM.id.in_(ids))
                .returning(QueueSessionORM.id)
            )
            sessions += len(deleted.all())
            await db.commit()
        removed = await db.execute(
            delete(StudentORM).where(*_orphans(cutoff)).returning(StudentORM.id)
        )
        students = len(removed.all())
        await db.commit()
    return PurgeResult(sessions, students)


async def retention_loop(
    factory: async_sessionmaker[AsyncSession], days: int, interval_seconds: float
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            result = await purge_closed_sessions(factory, days)
        except Exception:
            logger.exception("Retention cleanup failed")
            continue
        if result.sessions or result.students:
            metrics.inc("sessions_purged_total", result.sessions)
            logger.info(
                "Retention: removed %d closed sessions and %d unused students (older than %d days)",
                result.sessions,
                result.students,
                days,
            )


async def _run(days: int, apply: bool) -> PurgeResult:
    try:
        return await purge_closed_sessions(AsyncSessionLocal, days, dry_run=not apply)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m services.retention",
        description="Delete closed sessions older than the retention period",
    )
    parser.add_argument("--days", type=int, default=None, help="retention period in days")
    parser.add_argument(
        "--apply", action="store_true", help="actually delete (default: count only)"
    )
    args = parser.parse_args(argv)
    days = settings.DATA_RETENTION_DAYS if args.days is None else args.days
    if days < 0:
        parser.error("--days must not be negative")
    result = asyncio.run(_run(days, args.apply))
    verb = "removed" if args.apply else "would remove"
    print(
        f"{verb}: {result.sessions} closed sessions and {result.students} unused student records"
        f" older than {days} days"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
