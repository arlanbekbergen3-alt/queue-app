from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from core.dependencies import DbSession
from core.ratelimit import client_ip
from services.practice import (
    PracticeCheckIn,
    PracticeInfoOut,
    PracticeJobOut,
    PracticeService,
)

router = APIRouter(prefix="/practice", tags=["Practice"])


def get_practice_service(db: DbSession) -> PracticeService:
    return PracticeService(db)


PracticeServiceDep = Annotated[PracticeService, Depends(get_practice_service)]


@router.get("/{code}", response_model=PracticeInfoOut)
async def practice_info(code: str, service: PracticeServiceDep):
    return await service.info(code)


@router.post("/{code}/check", response_model=PracticeJobOut, status_code=status.HTTP_202_ACCEPTED)
async def practice_check(
    code: str, data: PracticeCheckIn, request: Request, service: PracticeServiceDep
):
    return await service.check(code, client_ip(request), data)


@router.get("/{code}/jobs/{job_id}", response_model=PracticeJobOut)
async def practice_job(code: str, job_id: str, service: PracticeServiceDep):
    return await service.job(code, job_id)
