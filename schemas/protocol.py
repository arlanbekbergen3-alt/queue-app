import unicodedata
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, Field, field_validator, model_validator


def _clean(value: str) -> str:
    value = value.strip()
    if any(unicodedata.category(ch) in ("Cc", "Cf") and ch not in "\n\t" for ch in value):
        raise ValueError("must not contain control characters")
    return value


def _not_blank(value: str) -> str:
    value = _clean(value)
    if not value:
        raise ValueError("must not be blank")
    return value


Text = Annotated[str, AfterValidator(_not_blank)]
OptionalText = Annotated[str, AfterValidator(_clean)]


class AssignmentCreate(BaseModel):
    title: Text = Field(min_length=1, max_length=120)
    description: OptionalText = Field(default="", max_length=4000)
    questions_per_submission: int = Field(default=3, ge=1, le=10)
    practice_enabled: bool = False
    is_template: bool = False


class AssignmentUpdate(BaseModel):
    title: Text | None = Field(default=None, min_length=1, max_length=120)
    description: OptionalText | None = Field(default=None, max_length=4000)
    questions_per_submission: int | None = Field(default=None, ge=1, le=10)
    practice_enabled: bool | None = None
    is_template: bool | None = None


class OriginOut(BaseModel):
    author: str
    space: str
    title: str


class AssignmentOut(BaseModel):
    id: str
    title: str
    description: str
    questions_per_submission: int
    practice_enabled: bool
    is_template: bool = False
    created_at: datetime
    question_count: int = 0
    criteria_count: int = 0
    submissions_count: int = 0
    criteria_locked: bool = False
    origin: OriginOut | None = None

    model_config = {"from_attributes": True}


class QuestionCreate(BaseModel):
    text: Text = Field(min_length=1, max_length=1000)
    difficulty: int = Field(default=2, ge=1, le=3)
    topic: OptionalText = Field(default="", max_length=60)


class QuestionUpdate(BaseModel):
    text: Text | None = Field(default=None, min_length=1, max_length=1000)
    difficulty: int | None = Field(default=None, ge=1, le=3)
    topic: OptionalText | None = Field(default=None, max_length=60)


class QuestionOut(BaseModel):
    id: str
    text: str
    difficulty: int
    topic: str
    position: int

    model_config = {"from_attributes": True}


MAX_LEVELS = 11


class LevelIn(BaseModel):
    points: int = Field(ge=0, le=100)
    text: Text = Field(min_length=1, max_length=200)


class LevelOut(BaseModel):
    points: int
    text: str


def _check_levels(levels: list[LevelIn] | None) -> list[LevelIn] | None:
    if levels is None:
        return None
    if len(levels) > MAX_LEVELS:
        raise ValueError("Too many levels")
    points = [level.points for level in levels]
    if len(set(points)) != len(points):
        raise ValueError("Level points must be unique")
    return sorted(levels, key=lambda level: level.points)


Levels = Annotated[list[LevelIn] | None, AfterValidator(_check_levels)]


class CriterionCreate(BaseModel):
    name: Text = Field(min_length=1, max_length=80)
    max_points: int = Field(ge=1, le=100)
    levels: Levels = None


class CriterionUpdate(BaseModel):
    name: Text | None = Field(default=None, min_length=1, max_length=80)
    max_points: int | None = Field(default=None, ge=1, le=100)


class LevelsIn(BaseModel):
    levels: Levels = None


class CriterionOut(BaseModel):
    id: str
    name: str
    max_points: int
    position: int
    levels: list[LevelOut] = Field(default_factory=list)

    model_config = {"from_attributes": True}

    @field_validator("levels", mode="before")
    @classmethod
    def _none_is_empty(cls, value):
        return value or []


class AssignmentCopyIn(BaseModel):
    title: Text | None = Field(default=None, min_length=1, max_length=120)


class SessionAssignmentIn(BaseModel):
    assignment_id: str | None = None


class ScoreIn(BaseModel):
    criterion_id: str = Field(min_length=1, max_length=36)
    points: int = Field(ge=0, le=100)
    comment: OptionalText = Field(default="", max_length=300)


class GradesIn(BaseModel):
    scores: list[ScoreIn] = Field(default_factory=list, max_length=20)
    comment: OptionalText = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def no_duplicate_criteria(self) -> "GradesIn":
        ids = [s.criterion_id for s in self.scores]
        if len(ids) != len(set(ids)):
            raise ValueError("each criterion may be scored once")
        return self


class CorrectionIn(BaseModel):
    scores: list[ScoreIn] = Field(default_factory=list, max_length=20)
    comment: OptionalText | None = Field(default=None, max_length=1000)
    reason: Text = Field(min_length=3, max_length=300)

    @model_validator(mode="after")
    def no_duplicate_criteria(self) -> "CorrectionIn":
        ids = [s.criterion_id for s in self.scores]
        if len(ids) != len(set(ids)):
            raise ValueError("each criterion may be scored once")
        return self


class SubmissionQuestionOut(BaseModel):
    position: int
    text: str
    difficulty: int
    topic: str

    model_config = {"from_attributes": True}


class ScoreOut(BaseModel):
    criterion_id: str
    name: str
    points: int
    max_points: int
    comment: str
    levels: list[LevelOut] = Field(default_factory=list)


class SubmissionOut(BaseModel):
    id: str
    entry_id: str
    ticket: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    duration_seconds: int | None
    total_points: int | None
    max_points: int | None
    comment: str
    questions: list[SubmissionQuestionOut]
    scores: list[ScoreOut]
    questions_short: bool = False
    attempt_number: int = 1
    bonus_points: int = 0
    selection_seed: str | None = None
    pool_question_ids: list[str] | None = None
    selection_usage: dict[str, int] | None = None


class MySubmissionOut(BaseModel):
    status: str
    attempt_number: int = 1
    questions: list[SubmissionQuestionOut] = []
    scores: list[ScoreOut] = []
    total_points: int | None = None
    max_points: int | None = None
    comment: str = ""
    bonus_points: int = 0


class BonusRemoveIn(BaseModel):
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("reason must not be blank")
        return value


class QuestionImportIn(BaseModel):
    text: str = Field(min_length=1, max_length=50000)
    difficulty: int = Field(default=2, ge=1, le=3)
    topic: str = Field(default="", max_length=60)
    dry_run: bool = True

    @field_validator("topic")
    @classmethod
    def strip_topic(cls, value: str) -> str:
        return " ".join(value.split())


class ImportLineOut(BaseModel):
    text: str
    status: str


class QuestionImportOut(BaseModel):
    lines: list[ImportLineOut]
    added: int
    remaining_capacity: int


class PresetCriterionOut(BaseModel):
    name: str
    max_points: int


class PresetOut(BaseModel):
    key: str
    title: str
    criteria: list[PresetCriterionOut]


class PresetApplyIn(BaseModel):
    preset: str = Field(min_length=1, max_length=40)


class AuditOut(BaseModel):
    seq: int
    occurred_at: datetime
    action: str
    field: str
    old_value: str | None
    new_value: str | None
    reason: str

    model_config = {"from_attributes": True}
