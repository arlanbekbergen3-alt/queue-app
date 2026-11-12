from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.user import UserORM


class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: str) -> UserORM | None:
        return await self.db.get(UserORM, user_id)

    async def get_by_username(self, username: str) -> UserORM | None:
        result = await self.db.execute(select(UserORM).where(UserORM.username == username))
        return result.scalar_one_or_none()

    def add(self, username: str, password_hash: str) -> UserORM:
        user = UserORM(username=username, password_hash=password_hash)
        self.db.add(user)
        return user
