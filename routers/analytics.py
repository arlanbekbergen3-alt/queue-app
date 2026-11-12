from typing import Annotated

from fastapi import APIRouter, Depends

from core.dependencies import CurrentTeacher, DbSession
from services.analytics import AnalyticsOut, AnalyticsService

router = APIRouter(tags=["Analytics"])


def get_analytics_service(db: DbSession) -> AnalyticsService:
    return AnalyticsService(db)


AnalyticsServiceDep = Annotated[AnalyticsService, Depends(get_analytics_service)]


@router.get("/assignments/{assignment_id}/analytics", response_model=AnalyticsOut)
async def assignment_analytics(
    assignment_id: str, teacher: CurrentTeacher, service: AnalyticsServiceDep
):
    return await service.for_assignment(teacher.id, assignment_id)


@router.get("/sessions/{code}/analytics", response_model=AnalyticsOut)
async def session_analytics(code: str, teacher: CurrentTeacher, service: AnalyticsServiceDep):
    return await service.for_session(code, teacher.id)
