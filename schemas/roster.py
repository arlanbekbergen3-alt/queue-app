from pydantic import BaseModel, Field


class RosterPut(BaseModel):
    group: str = Field(min_length=1, max_length=120)
    text: str = Field(max_length=20000)


class RosterGroupOut(BaseModel):
    group: str
    count: int


class RosterSkippedOut(BaseModel):
    text: str
    reason: str


class RosterOut(BaseModel):
    group: str
    names: list[str]
    skipped: list[RosterSkippedOut] = Field(default_factory=list)


class PublicRosterOut(BaseModel):
    names: list[str]
    only: bool
