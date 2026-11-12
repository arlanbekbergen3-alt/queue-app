from typing import Annotated

import jwt
from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from core.exceptions import UnauthorizedError
from core.security import decode_access_token
from db.database import get_db
from models.user import UserORM
from repositories.user import UserRepository
from services.auth import AuthService
from services.protocol import ProtocolService
from services.queue import QueueService

bearer_scheme = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_current_teacher(
    db: DbSession,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> UserORM:
    if credentials is None:
        raise UnauthorizedError("Not authenticated", code="not_authenticated")
    try:
        user_id = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise UnauthorizedError("Invalid token", code="invalid_token") from None
    user = await UserRepository(db).get_by_id(user_id)
    if user is None:
        raise UnauthorizedError("User not found", code="user_not_found")
    return user


def get_entry_token(
    x_entry_token: Annotated[str | None, Header(min_length=16, max_length=128)] = None,
) -> str:
    if x_entry_token is None:
        raise UnauthorizedError("Missing X-Entry-Token header", code="missing_entry_token")
    return x_entry_token


CurrentTeacher = Annotated[UserORM, Depends(get_current_teacher)]
EntryToken = Annotated[str, Depends(get_entry_token)]


def get_auth_service(db: DbSession) -> AuthService:
    return AuthService(db)


def get_queue_service(db: DbSession) -> QueueService:
    return QueueService(db)


def get_protocol_service(db: DbSession) -> ProtocolService:
    return ProtocolService(db)


ProtocolServiceDep = Annotated[ProtocolService, Depends(get_protocol_service)]
AuthServiceDep = Annotated[AuthService, Depends(get_auth_service)]
QueueServiceDep = Annotated[QueueService, Depends(get_queue_service)]
