from typing import Annotated

from fastapi import APIRouter, Depends, Query

from core.dependencies import CurrentTeacher, DbSession, EntryToken
from core.ratelimit import limit_token
from schemas.history import (
    MergeIn,
    MyHistoryOut,
    ReassignIn,
    StudentCardOut,
    StudentListItem,
)
from services.history import HistoryService

router = APIRouter(tags=["History"])


def get_history_service(db: DbSession) -> HistoryService:
    return HistoryService(db)


HistoryServiceDep = Annotated[HistoryService, Depends(get_history_service)]


@router.get("/students", response_model=list[StudentListItem])
async def list_students(
    teacher: CurrentTeacher,
    service: HistoryServiceDep,
    group: Annotated[str | None, Query(max_length=60)] = None,
    assignment_id: Annotated[str | None, Query(max_length=36)] = None,
    q: Annotated[str | None, Query(max_length=60)] = None,
):
    return await service.list_students(teacher.id, group, assignment_id, q)


@router.get("/students/{student_id}", response_model=StudentCardOut)
async def student_card(
    student_id: str,
    teacher: CurrentTeacher,
    service: HistoryServiceDep,
    assignment_id: Annotated[str | None, Query(max_length=36)] = None,
):
    return await service.card(teacher.id, student_id, assignment_id)


@router.post("/students/{student_id}/merge", response_model=StudentCardOut)
async def merge_students(
    student_id: str, data: MergeIn, teacher: CurrentTeacher, service: HistoryServiceDep
):
    return await service.merge(teacher.id, student_id, data.into_student_id)


@router.post("/submissions/{submission_id}/student", response_model=StudentCardOut)
async def reassign_submission(
    submission_id: str, data: ReassignIn, teacher: CurrentTeacher, service: HistoryServiceDep
):
    return await service.reassign(teacher.id, submission_id, data.student_id)


@router.get(
    "/sessions/{code}/me/history",
    response_model=MyHistoryOut,
    dependencies=[Depends(limit_token)],
)
async def my_history(code: str, token: EntryToken, service: HistoryServiceDep):
    return await service.my_history(code, token)
