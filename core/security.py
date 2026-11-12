import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

_DUMMY_HASH = pwd_context.hash("dummy-password")


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(pwd_context.hash, password)


async def verify_password(password: str, password_hash: str | None) -> bool:
    valid = await asyncio.to_thread(pwd_context.verify, password, password_hash or _DUMMY_HASH)
    return valid and password_hash is not None


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode(
        {"sub": user_id, "exp": expire},
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )


def decode_access_token(token: str) -> str:
    payload = jwt.decode(
        token,
        settings.JWT_SECRET_KEY,
        algorithms=[settings.JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    return str(payload["sub"])
