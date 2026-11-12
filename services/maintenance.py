import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.events import notify_changed
from core.metrics import metrics
from repositories.queue import QueueRepository
from services.protocol_calls import cancel_open_submissions, session_ids_by_code

logger = logging.getLogger(__name__)


async def close_idle_sessions(
    factory: async_sessionmaker[AsyncSession], hours: float, *, now: datetime | None = None
) -> list[str]:
    moment = now or datetime.now(UTC)
    cutoff = moment - timedelta(hours=hours)
    async with factory() as db:
        codes = await QueueRepository(db).close_idle_sessions(cutoff, moment)
        await cancel_open_submissions(db, await session_ids_by_code(db, codes))
        await db.commit()
    for code in codes:
        notify_changed(code)
        metrics.inc("sessions_auto_closed_total")
    if codes:
        logger.info("Auto-closed %d idle sessions: %s", len(codes), ", ".join(codes))
    return codes


async def reaper_loop(
    factory: async_sessionmaker[AsyncSession], hours: float, interval_seconds: float
) -> None:
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await close_idle_sessions(factory, hours)
        except Exception:
            logger.exception("Idle session cleanup failed")
