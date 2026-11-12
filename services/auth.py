import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import ConflictError, ForbiddenError, UnauthorizedError
from core.security import create_access_token, hash_password, verify_password
from models.user import UserORM
from repositories.user import UserRepository
from schemas.auth import LoginIn, RegisterIn


class AuthService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.users = UserRepository(db)

    async def register(self, data: RegisterIn) -> UserORM:
        if not hmac.compare_digest(
            data.invite_code.encode(), settings.TEACHER_INVITE_CODE.encode()
        ):
            raise ForbiddenError("Invalid invite code", code="invalid_invite_code")
        if await self.users.get_by_username(data.username) is not None:
            raise ConflictError("Username is already taken", code="username_taken")
        user = self.users.add(data.username, await hash_password(data.password))
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError("Username is already taken", code="username_taken") from None
        return user

    async def login(self, data: LoginIn) -> str:
        user = await self.users.get_by_username(data.username)
        valid = await verify_password(data.password, user.password_hash if user else None)
        if user is None or not valid:
            raise UnauthorizedError("Incorrect username or password", code="invalid_credentials")
        return create_access_token(user.id)
