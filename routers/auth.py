from fastapi import APIRouter, Depends, status

from core.dependencies import AuthServiceDep, CurrentTeacher
from core.ratelimit import limit_auth
from schemas.auth import LoginIn, RegisterIn, TeacherOut, TokenOut

router = APIRouter(prefix="/auth", tags=["Auth"])


@router.post(
    "/register",
    response_model=TeacherOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(limit_auth)],
)
async def register(data: RegisterIn, service: AuthServiceDep):
    return await service.register(data)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_auth)])
async def login(data: LoginIn, service: AuthServiceDep):
    return TokenOut(access_token=await service.login(data))


@router.get("/me", response_model=TeacherOut)
async def me(teacher: CurrentTeacher):
    return teacher
