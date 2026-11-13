from typing import Annotated

from fastapi import APIRouter, Depends, status

from core.dependencies import CurrentTeacher, DbSession
from schemas.protocol import AssignmentCopyIn, AssignmentOut
from services.assignment_copy import AssignmentCopyService

router = APIRouter(tags=["Templates"])


def get_copy_service(db: DbSession) -> AssignmentCopyService:
    return AssignmentCopyService(db)


CopyServiceDep = Annotated[AssignmentCopyService, Depends(get_copy_service)]


@router.post(
    "/assignments/{assignment_id}/copy",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def copy_assignment(
    assignment_id: str, data: AssignmentCopyIn, teacher: CurrentTeacher, service: CopyServiceDep
):
    return await service.copy(teacher.id, assignment_id, data.title)


@router.get("/assignment-templates", response_model=list[AssignmentOut])
async def list_templates(teacher: CurrentTeacher, service: CopyServiceDep):
    return await service.templates(teacher.id)


@router.post(
    "/assignment-templates/{template_id}/create",
    response_model=AssignmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_template(
    template_id: str, data: AssignmentCopyIn, teacher: CurrentTeacher, service: CopyServiceDep
):
    return await service.copy(teacher.id, template_id, data.title, template_only=True)
