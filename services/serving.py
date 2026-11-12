from datetime import UTC, datetime

from models.entry import EntryORM, EntryStatus
from models.queue_session import QueueSessionORM
from repositories.queue import QueueRepository

MIN_SERVICE_SECONDS = 30
MAX_SERVICE_SECONDS = 3600
AVERAGE_SMOOTHING = 0.3


async def mark_entry_done(repo: QueueRepository, session: QueueSessionORM, entry: EntryORM) -> None:
    now = datetime.now(UTC)
    entry.status = EntryStatus.DONE
    entry.finished_at = now
    if entry.called_at is not None:
        spent = (now - entry.called_at).total_seconds()
        spent = min(max(spent, MIN_SERVICE_SECONDS), MAX_SERVICE_SECONDS)
        average = round(
            (1 - AVERAGE_SMOOTHING) * session.avg_service_seconds + AVERAGE_SMOOTHING * spent
        )
        await repo.set_average(session.id, average)
