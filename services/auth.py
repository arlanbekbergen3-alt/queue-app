import hmac

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.attempts import AttemptGuard
from core.config import settings
from core.exceptions import ConflictError, ForbiddenError, RateLimitError, UnauthorizedError
from core.security import create_access_token, hash_password, verify_password
from models.user import UserORM
from repositories.user import UserRepository
from schemas.auth import LoginIn, RegisterIn

login_guard = AttemptGuard(
    ip_failures=settings.LOGIN_FAILURES_PER_IP,
    session_failures=settings.LOGIN_FAILURES_PER_USER,
    window_seconds=float(settings.LOGIN_FAILURE_WINDOW_SECONDS),
    lock_seconds=float(settings.LOGIN_LOCK_SECONDS),
)


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

    async def login(self, data: LoginIn, client_ip: str = "-") -> str:
        key = data.username.casefold()
        wait = login_guard.retry_after(key, client_ip)
        if wait:
            raise RateLimitError(
                f"Слишком много неудачных попыток входа. Подождите {max(1, wait // 60)} мин",
                headers={"Retry-After": str(wait)},
                code="login_locked",
            )
        user = await self.users.get_by_username(data.username)
        valid = await verify_password(data.password, user.password_hash if user else None)
        if user is None or not valid:
            login_guard.failed(key, client_ip)
            raise UnauthorizedError("Incorrect username or password", code="invalid_credentials")
        login_guard.clear(key, client_ip)
        return create_access_token(user.id)
