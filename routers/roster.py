from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from core.dependencies import CurrentTeacher, RosterServiceDep
from schemas.roster import RosterGroupOut, RosterOut, RosterPut

router = APIRouter(prefix="/roster", tags=["Roster"])

GroupQuery = Annotated[str, Query(min_length=1, max_length=120)]


@router.get("", response_model=list[RosterGroupOut])
async def list_groups(teacher: CurrentTeacher, service: RosterServiceDep):
    return await service.groups(teacher.id)


@router.get("/names", response_model=RosterOut)
async def get_group(group: GroupQuery, teacher: CurrentTeacher, service: RosterServiceDep):
    return await service.get(teacher.id, group)


@router.put("", response_model=RosterOut)
async def replace_group(data: RosterPut, teacher: CurrentTeacher, service: RosterServiceDep):
    return await service.replace(teacher.id, data.group, data.text)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_group(group: GroupQuery, teacher: CurrentTeacher, service: RosterServiceDep):
    await service.delete(teacher.id, group)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
