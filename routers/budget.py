from typing import Annotated

from fastapi import APIRouter, Depends

from core.dependencies import CurrentTeacher, DbSession
from schemas.budget import BudgetDecisionIn, BudgetOut
from services.budget import BudgetService

router = APIRouter(prefix="/sessions", tags=["Budget"])


def get_budget_service(db: DbSession) -> BudgetService:
    return BudgetService(db)


BudgetServiceDep = Annotated[BudgetService, Depends(get_budget_service)]


@router.get("/{code}/budget", response_model=BudgetOut)
async def budget(code: str, teacher: CurrentTeacher, service: BudgetServiceDep):
    return await service.view(code, teacher.id)


@router.post("/{code}/budget/decision", response_model=BudgetOut)
async def decide(
    code: str, data: BudgetDecisionIn, teacher: CurrentTeacher, service: BudgetServiceDep
):
    return await service.decide(code, teacher.id, data)
