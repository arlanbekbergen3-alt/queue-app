from typing import Annotated

from fastapi import APIRouter, Depends, Response, status

from core.dependencies import CurrentTeacher, DbSession
from schemas.protocol import AssignmentCopyIn, AssignmentOut
from schemas.spaces import (
    PublicationDetailOut,
    PublicationOut,
    PublishIn,
    SpaceCreate,
    SpaceDetailOut,
    SpaceJoin,
    SpaceOut,
)
from services.spaces import SpaceService

router = APIRouter(prefix="/spaces", tags=["Spaces"])


def get_space_service(db: DbSession) -> SpaceService:
    return SpaceService(db)


SpaceServiceDep = Annotated[SpaceService, Depends(get_space_service)]


@router.post("", response_model=SpaceOut, status_code=status.HTTP_201_CREATED)
async def create_space(data: SpaceCreate, teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.create(teacher, data.name)


@router.get("", response_model=list[SpaceOut])
async def list_spaces(teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.list_for(teacher)


@router.post("/join", response_model=SpaceOut)
async def join_space(data: SpaceJoin, teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.join(teacher, data.code)


@router.get("/{space_id}", response_model=SpaceDetailOut)
async def get_space(space_id: str, teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.detail(teacher, space_id)


@router.delete("/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_space(space_id: str, teacher: CurrentTeacher, service: SpaceServiceDep):
    await service.delete(teacher, space_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{space_id}/invite", response_model=SpaceOut)
async def regenerate_invite(space_id: str, teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.regenerate_invite(teacher, space_id)


@router.delete("/{space_id}/members/me", status_code=status.HTTP_204_NO_CONTENT)
async def leave_space(space_id: str, teacher: CurrentTeacher, service: SpaceServiceDep):
    await service.leave(teacher, space_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{space_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_member(
    space_id: str, user_id: str, teacher: CurrentTeacher, service: SpaceServiceDep
):
    await service.remove_member(teacher, space_id, user_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{space_id}/publications", response_model=list[PublicationOut])
async def list_publications(space_id: str, teacher: CurrentTeacher, service: SpaceServiceDep):
    return await service.list_publications(teacher, space_id)


@router.post(
    "/{space_id}/publications",
    response_model=PublicationDetailOut,
    status_code=status.HTTP_201_CREATED,
)
async def publish(
    space_id: str, data: PublishIn, teacher: CurrentTeacher, service: SpaceServiceDep
):
    return await service.publish(teacher, space_id, data.assignment_id, data.title)


@router.get("/{space_id}/publications/{publication_id}", response_model=PublicationDetailOut)
async def get_publication(
    space_id: str, publication_id: str, teacher: CurrentTeacher, service: SpaceServiceDep
):
    return await service.publication(teacher, space_id, publication_id)


@router.delete("/{space_id}/publications/{publication_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unpublish(
    space_id: str, publication_id: str, teacher: CurrentTeacher, service: SpaceServiceDep
):
    await service.unpublish(teacher, space_id, publication_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{space_id}/publications/{publication_id}/copy",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def copy_publication(
    space_id: str,
    publication_id: str,
    data: AssignmentCopyIn,
    teacher: CurrentTeacher,
    service: SpaceServiceDep,
):
    return await service.copy(teacher, space_id, publication_id, data.title)
