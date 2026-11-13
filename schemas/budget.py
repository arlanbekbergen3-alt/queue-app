from datetime import datetime

from pydantic import BaseModel, Field


class SuggestionOut(BaseModel):
    questions_from: int
    questions_to: int
    forecast_end_at: datetime


class BudgetOut(BaseModel):
    enabled: bool
    status: str | None = None
    deadline_at: datetime | None = None
    remaining_seconds: int | None = None
    remaining_students: int | None = None
    reserve_seconds: int | None = None
    budget_seconds: int | None = None
    avg_service_seconds: int | None = None
    forecast_end_at: datetime | None = None
    questions: int | None = None
    suggestion: SuggestionOut | None = None


class BudgetDecisionIn(BaseModel):
    accept: bool
    questions: int = Field(ge=1, le=10)
