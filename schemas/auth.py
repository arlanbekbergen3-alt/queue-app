from pydantic import BaseModel, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=8, max_length=128)
    invite_code: str = Field(min_length=1, max_length=128)


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class TeacherOut(BaseModel):
    id: str
    username: str

    model_config = {"from_attributes": True}
