from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from core.config import settings
from core.dependencies import CurrentTeacher, DbSession, EntryToken, QueueServiceDep
from core.exceptions import NotFoundError
from core.ratelimit import limit_token
from services.live import hub, student_stream, teacher_stream

router = APIRouter(prefix="/sessions", tags=["Live"])

SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _response(stream: AsyncIterator[str]) -> StreamingResponse:
    return StreamingResponse(stream, media_type="text/event-stream", headers=SSE_HEADERS)


@router.get("/{code}/events", dependencies=[Depends(limit_token)])
async def student_events(code: str, token: EntryToken, db: DbSession, service: QueueServiceDep):
    if not settings.SSE_ENABLED:
        raise NotFoundError("Live updates are disabled", code="live_disabled")
    session_code, entry_id = await service.live_entry_id(code, token)
    await db.close()
    hub.check_capacity(session_code)
    return _response(student_stream(session_code, entry_id))


@router.get("/{code}/queue/events")
async def teacher_events(
    code: str, teacher: CurrentTeacher, db: DbSession, service: QueueServiceDep
):
    if not settings.SSE_ENABLED:
        raise NotFoundError("Live updates are disabled", code="live_disabled")
    session_code = await service.live_teacher_code(code, teacher.id)
    await db.close()
    hub.check_capacity(session_code)
    return _response(teacher_stream(session_code))
