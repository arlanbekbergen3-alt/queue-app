from typing import Annotated

from fastapi import APIRouter, Depends, status

from core.config import settings
from core.dependencies import CurrentTeacher, DbSession
from services.ai_drafts import ApproveIn, ApproveOut, DraftRequest, DraftService, JobOut

router = APIRouter(tags=["AI"])


def get_draft_service(db: DbSession) -> DraftService:
    return DraftService(db)


DraftServiceDep = Annotated[DraftService, Depends(get_draft_service)]


@router.get("/ai/status")
async def ai_status(teacher: CurrentTeacher):
    return {
        "enabled": settings.AI_ENABLED,
        "model": settings.AI_MODEL if settings.AI_ENABLED else None,
        "max_source_chars": settings.AI_MAX_SOURCE_CHARS,
    }


@router.post(
    "/assignments/{assignment_id}/ai/draft",
    response_model=JobOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def start_draft(
    assignment_id: str, data: DraftRequest, teacher: CurrentTeacher, service: DraftServiceDep
):
    return await service.create(teacher.id, assignment_id, data.source_text)


@router.get("/ai/jobs/{job_id}", response_model=JobOut)
async def get_job(job_id: str, teacher: CurrentTeacher, service: DraftServiceDep):
    return await service.get(teacher.id, job_id)


@router.post("/ai/jobs/{job_id}/approve", response_model=ApproveOut)
async def approve_job(
    job_id: str, data: ApproveIn, teacher: CurrentTeacher, service: DraftServiceDep
):
    return await service.approve(teacher.id, job_id, data)
