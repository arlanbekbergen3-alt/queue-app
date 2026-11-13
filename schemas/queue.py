import unicodedata
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from core.config import settings


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    group: str | None = Field(default=None, max_length=60)
    order_mode: Literal["join", "lottery"] = "join"
    session_mode: Literal["queue_only", "quick", "protocol"] = "protocol"
    quick_max_points: int = Field(default=10, ge=1, le=100)
    ready_priority: bool = False
    ready_bonus_points: int = Field(default=0, ge=0, le=5)
    ready_skip_questions: int = Field(default=0, ge=0, le=1)
    duration_minutes: int | None = Field(default=None, ge=1, le=720)
    deadline_at: datetime | None = None

    @model_validator(mode="after")
    def one_time_limit(self) -> "SessionCreate":
        if self.duration_minutes is not None and self.deadline_at is not None:
            raise ValueError("set either duration_minutes or deadline_at")
        if self.deadline_at is not None and self.deadline_at.tzinfo is None:
            raise ValueError("deadline_at must include a time zone")
        return self

    @field_validator("group")
    @classmethod
    def clean_group(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value):
            raise ValueError("group must not contain control characters")
        return " ".join(value.split()) or None

    @field_validator("title")
    @classmethod
    def strip_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("title must not be blank")
        return value


class SessionSettingsIn(BaseModel):
    order_mode: Literal["join", "lottery"] | None = None
    ready_priority: bool | None = None
    ready_bonus_points: int | None = Field(default=None, ge=0, le=5)
    ready_skip_questions: int | None = Field(default=None, ge=0, le=1)


class ReadyNowIn(BaseModel):
    ready_now: bool


class NowServing(BaseModel):
    ticket: int
    name: str


class SessionOut(BaseModel):
    code: str
    title: str
    group: str | None = Field(default=None, validation_alias="group_name")
    status: str
    avg_service_seconds: int
    created_at: datetime
    assignment_id: str | None = None
    join_open: bool = True
    order_mode: str = "join"
    ready_priority: bool = False
    ready_bonus_points: int = 0
    ready_skip_questions: int = 0
    lottery_at: datetime | None = None
    deadline_at: datetime | None = None
    questions_override: int | None = None
    session_mode: str = "protocol"
    quick_max_points: int = 10

    model_config = {"from_attributes": True}


class SessionPublic(BaseModel):
    code: str
    title: str
    group: str | None
    status: str
    waiting_count: int
    now_serving: NowServing | None
    practice: bool = False
    join_open: bool = True
    order_mode: str = "join"
    ready_priority: bool = False
    ready_bonus_points: int = 0
    ready_skip_questions: int = 0
    lottery_done: bool = False
    session_mode: str = "protocol"


class RegistrationIn(BaseModel):
    open: bool


class JoinIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    token: str = Field(min_length=16, max_length=128)
    device_key: str | None = Field(default=None, min_length=16, max_length=128)
    ready_now: bool = False

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in value if ch not in (" ", chr(9))):
            raise ValueError("name must not contain control characters")
        value = " ".join(value.split())
        if not value:
            raise ValueError("name must not be blank")
        if len(value) > settings.NAME_MAX_LENGTH:
            raise ValueError(f"name must be at most {settings.NAME_MAX_LENGTH} characters")
        return value


class MyEntryOut(BaseModel):
    ticket: int | None
    name: str
    status: str
    position: int | None
    people_ahead: int | None
    eta_seconds: int | None
    session_status: str
    now_serving: NowServing | None
    not_ready: bool = False
    not_ready_count: int = 0
    not_ready_left: int = 0
    ready_now: bool = False
    in_pool: bool = False
    pool_count: int = 0
    join_open: bool = True
    order_mode: str = "join"
    ready_priority: bool = False
    ready_bonus_points: int = 0
    ready_skip_questions: int = 0


class TeacherEntryOut(BaseModel):
    id: str
    ticket: int
    name: str
    status: str
    joined_at: datetime
    called_at: datetime | None
    not_ready: bool = False
    not_ready_count: int = 0
    ready_now: bool = False

    model_config = {"from_attributes": True}


class TeacherQueueOut(BaseModel):
    code: str
    title: str
    group: str | None
    status: str
    avg_service_seconds: int
    waiting_count: int
    entries: list[TeacherEntryOut]
    join_open: bool = True
    not_ready_max: int = 2
    order_mode: str = "join"
    ready_priority: bool = False
    ready_bonus_points: int = 0
    ready_skip_questions: int = 0
    lottery_done: bool = False
    session_mode: str = "protocol"
    quick_max_points: int = 10
    assignment_id: str | None = None
