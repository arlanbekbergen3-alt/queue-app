from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HistoryScoreOut(BaseModel):
    name: str
    points: int
    max_points: int


class HistoryQuestionOut(BaseModel):
    text: str
    difficulty: int
    topic: str


class AttemptOut(BaseModel):
    submission_id: str
    session_code: str
    session_title: str
    attempt_number: int | None
    status: str
    started_at: datetime
    ended_at: datetime | None
    total_points: int | None
    max_points: int | None
    bonus_points: int = 0
    percent: float | None
    delta_percent: float | None
    comment: str
    scores: list[HistoryScoreOut]
    questions: list[HistoryQuestionOut]


class SeenQuestionOut(BaseModel):
    text: str
    times: int


class AssignmentHistoryOut(BaseModel):
    assignment_id: str
    title: str
    attempts: list[AttemptOut]
    seen_questions: list[SeenQuestionOut]


class StudentCardOut(BaseModel):
    id: str
    name: str
    group: str | None
    names: list[str]
    assignments: list[AssignmentHistoryOut]


class StudentListItem(BaseModel):
    id: str
    name: str
    group: str | None
    submissions: int
    last_started_at: datetime | None


class MergeIn(BaseModel):
    into_student_id: str = Field(min_length=1, max_length=36)


class ReassignIn(BaseModel):
    student_id: str | None = Field(default=None, max_length=36)


class MyAttemptOut(BaseModel):
    assignment_title: str
    attempt_number: int
    ended_at: datetime | None
    total_points: int | None
    max_points: int | None
    bonus_points: int = 0
    percent: float | None
    delta_percent: float | None
    comment: str
    scores: list[HistoryScoreOut]
    questions: list[HistoryQuestionOut]


class MyHistoryOut(BaseModel):
    attempts: list[MyAttemptOut]


class SessionEventOut(BaseModel):
    seq: int
    occurred_at: datetime
    kind: str
    details: dict[str, Any]

    model_config = {"from_attributes": True}
