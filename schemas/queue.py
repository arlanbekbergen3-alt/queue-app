import unicodedata
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from core.config import settings


class SessionCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    group: str | None = Field(default=None, max_length=60)

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

    model_config = {"from_attributes": True}


class SessionPublic(BaseModel):
    code: str
    title: str
    group: str | None
    status: str
    waiting_count: int
    now_serving: NowServing | None
    practice: bool = False


class JoinIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    token: str = Field(min_length=16, max_length=128)

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
    ticket: int
    name: str
    status: str
    position: int | None
    people_ahead: int | None
    eta_seconds: int | None
    session_status: str
    now_serving: NowServing | None


class TeacherEntryOut(BaseModel):
    id: str
    ticket: int
    name: str
    status: str
    joined_at: datetime
    called_at: datetime | None

    model_config = {"from_attributes": True}


class TeacherQueueOut(BaseModel):
    code: str
    title: str
    group: str | None
    status: str
    avg_service_seconds: int
    waiting_count: int
    entries: list[TeacherEntryOut]
