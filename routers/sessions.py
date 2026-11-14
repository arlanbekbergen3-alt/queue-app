from fastapi import APIRouter, Depends, Request, Response, status

from core.dependencies import CurrentTeacher, EntryToken, QueueServiceDep
from core.ratelimit import client_ip, limit_join, limit_token
from schemas.history import SessionEventOut
from schemas.queue import (
    JoinIn,
    MyEntryOut,
    PresenceIn,
    PresenceOut,
    ReadyNowIn,
    RegistrationIn,
    SessionCreate,
    SessionOut,
    SessionPublic,
    SessionSettingsIn,
    TeacherEntryOut,
    TeacherQueueOut,
)
from schemas.roster import PublicRosterOut

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.post("", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
async def create_session(data: SessionCreate, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.create_session(teacher.id, data)


@router.get("", response_model=list[SessionOut])
async def list_sessions(teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.list_sessions(teacher.id)


@router.get("/{code}", response_model=SessionPublic)
async def session_info(code: str, service: QueueServiceDep):
    return await service.public_info(code)


@router.get("/{code}/roster", response_model=PublicRosterOut)
async def session_roster(code: str, service: QueueServiceDep):
    return await service.public_roster(code)


@router.post("/{code}/join", response_model=MyEntryOut, dependencies=[Depends(limit_join)])
async def join(code: str, data: JoinIn, request: Request, service: QueueServiceDep):
    return await service.join(code, data, client_ip(request))


@router.get("/{code}/me", response_model=MyEntryOut, dependencies=[Depends(limit_token)])
async def my_status(code: str, token: EntryToken, service: QueueServiceDep):
    return await service.my_status(code, token)


@router.post("/{code}/leave", response_model=MyEntryOut, dependencies=[Depends(limit_token)])
async def leave(code: str, token: EntryToken, service: QueueServiceDep):
    return await service.leave(code, token)


@router.post("/{code}/not-ready", response_model=MyEntryOut, dependencies=[Depends(limit_token)])
async def not_ready(code: str, token: EntryToken, service: QueueServiceDep):
    return await service.mark_not_ready(code, token)


@router.post("/{code}/ready", response_model=MyEntryOut, dependencies=[Depends(limit_token)])
async def ready(code: str, token: EntryToken, service: QueueServiceDep):
    return await service.mark_ready(code, token)


@router.put("/{code}/priority", response_model=MyEntryOut, dependencies=[Depends(limit_token)])
async def set_priority(code: str, data: ReadyNowIn, token: EntryToken, service: QueueServiceDep):
    return await service.set_ready_now(code, token, data.ready_now)


@router.put("/{code}/settings", response_model=SessionOut)
async def update_settings(
    code: str, data: SessionSettingsIn, teacher: CurrentTeacher, service: QueueServiceDep
):
    return await service.update_settings(code, teacher.id, data)


@router.post("/{code}/lottery", response_model=SessionOut)
async def run_lottery(code: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.run_lottery(code, teacher.id)


@router.get("/{code}/events-log", response_model=list[SessionEventOut])
async def session_events(code: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.session_events(code, teacher.id)


@router.put("/{code}/presence", response_model=PresenceOut)
async def set_presence(
    code: str, data: PresenceIn, teacher: CurrentTeacher, service: QueueServiceDep
):
    return await service.set_presence(code, teacher.id, data)


@router.get("/{code}/presence", response_model=PresenceOut)
async def get_presence(
    code: str, response: Response, teacher: CurrentTeacher, service: QueueServiceDep
):
    response.headers["Cache-Control"] = "no-store"
    return await service.get_presence(code, teacher.id)


@router.put("/{code}/registration", response_model=SessionOut)
async def set_registration(
    code: str, data: RegistrationIn, teacher: CurrentTeacher, service: QueueServiceDep
):
    return await service.set_registration(code, teacher.id, data.open)


@router.get("/{code}/queue", response_model=TeacherQueueOut)
async def teacher_queue(code: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.teacher_queue(code, teacher.id)


@router.post("/{code}/close", response_model=SessionOut)
async def close_session(code: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.close_session(code, teacher.id)


@router.post("/{code}/next", response_model=TeacherEntryOut)
async def call_next(code: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.call_next(code, teacher.id)


@router.post("/{code}/entries/{entry_id}/done", response_model=TeacherEntryOut)
async def done(code: str, entry_id: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.finish(code, teacher.id, entry_id)


@router.post("/{code}/entries/{entry_id}/skip", response_model=TeacherEntryOut)
async def skip(code: str, entry_id: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.skip(code, teacher.id, entry_id)


@router.post("/{code}/entries/{entry_id}/requeue", response_model=TeacherEntryOut)
async def requeue(code: str, entry_id: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.requeue(code, teacher.id, entry_id)


@router.post("/{code}/entries/{entry_id}/promote", response_model=TeacherEntryOut)
async def promote(code: str, entry_id: str, teacher: CurrentTeacher, service: QueueServiceDep):
    return await service.promote(code, teacher.id, entry_id)
