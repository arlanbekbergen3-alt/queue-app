from datetime import datetime

from pydantic import BaseModel, Field

from schemas.protocol import Text


class SpaceCreate(BaseModel):
    name: Text = Field(min_length=1, max_length=80)


class SpaceJoin(BaseModel):
    code: Text = Field(min_length=4, max_length=32)


class PublishIn(BaseModel):
    assignment_id: str = Field(min_length=1, max_length=64)
    title: Text | None = Field(default=None, min_length=1, max_length=120)


class SpaceOut(BaseModel):
    id: str
    name: str
    role: str
    members_count: int
    publications_count: int
    invite_code: str | None = None
    created_at: datetime


class MemberOut(BaseModel):
    user_id: str
    username: str
    is_owner: bool
    joined_at: datetime


class SpaceDetailOut(SpaceOut):
    members: list[MemberOut]


class PublicationOut(BaseModel):
    id: str
    title: str
    author: str
    mine: bool
    published_at: datetime
    criteria_count: int
    questions_count: int


class PubCriterionOut(BaseModel):
    name: str
    max_points: int
    position: int

    model_config = {"from_attributes": True}


class PubQuestionOut(BaseModel):
    text: str
    difficulty: int
    topic: str
    position: int

    model_config = {"from_attributes": True}


class PublicationDetailOut(PublicationOut):
    criteria: list[PubCriterionOut]
    questions: list[PubQuestionOut]
