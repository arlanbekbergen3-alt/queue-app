from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models.space import (
    PublicationCriterionORM,
    PublicationORM,
    PublicationQuestionORM,
    SpaceMemberORM,
    SubjectSpaceORM,
)
from models.user import UserORM


class SpaceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def lock_user(self, user_id: str) -> None:
        await self.db.execute(
            select(func.pg_advisory_xact_lock(func.hashtext("spaces:" + user_id)))
        )

    async def get_space(self, space_id: str, *, lock: bool = False) -> SubjectSpaceORM | None:
        stmt = select(SubjectSpaceORM).where(SubjectSpaceORM.id == space_id)
        if lock:
            stmt = stmt.with_for_update().execution_options(populate_existing=True)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def space_by_code(self, code: str) -> SubjectSpaceORM | None:
        stmt = select(SubjectSpaceORM).where(SubjectSpaceORM.invite_code == code)
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def membership(self, space_id: str, user_id: str) -> SpaceMemberORM | None:
        stmt = select(SpaceMemberORM).where(
            SpaceMemberORM.space_id == space_id, SpaceMemberORM.user_id == user_id
        )
        return (await self.db.execute(stmt)).scalar_one_or_none()

    async def count_members(self, space_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(SpaceMemberORM)
            .where(SpaceMemberORM.space_id == space_id)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_memberships(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(SpaceMemberORM)
            .where(SpaceMemberORM.user_id == user_id)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_owned(self, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(SubjectSpaceORM)
            .where(SubjectSpaceORM.owner_id == user_id)
        )
        return (await self.db.execute(stmt)).scalar_one()

    async def count_publications(self, space_id: str, author_id: str | None = None) -> int:
        stmt = (
            select(func.count())
            .select_from(PublicationORM)
            .where(PublicationORM.space_id == space_id)
        )
        if author_id is not None:
            stmt = stmt.where(PublicationORM.author_id == author_id)
        return (await self.db.execute(stmt)).scalar_one()

    async def spaces_of(self, user_id: str) -> list[SubjectSpaceORM]:
        stmt = (
            select(SubjectSpaceORM)
            .join(SpaceMemberORM, SpaceMemberORM.space_id == SubjectSpaceORM.id)
            .where(SpaceMemberORM.user_id == user_id)
            .order_by(SubjectSpaceORM.created_at, SubjectSpaceORM.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def members(self, space_id: str) -> list[tuple[SpaceMemberORM, str]]:
        stmt = (
            select(SpaceMemberORM, UserORM.username)
            .join(UserORM, UserORM.id == SpaceMemberORM.user_id)
            .where(SpaceMemberORM.space_id == space_id)
            .order_by(SpaceMemberORM.joined_at, SpaceMemberORM.id)
        )
        return [(row[0], row[1]) for row in (await self.db.execute(stmt)).all()]

    async def publications(self, space_id: str) -> list[tuple[PublicationORM, str, int, int]]:
        criteria = (
            select(func.count())
            .where(PublicationCriterionORM.publication_id == PublicationORM.id)
            .scalar_subquery()
        )
        questions = (
            select(func.count())
            .where(PublicationQuestionORM.publication_id == PublicationORM.id)
            .scalar_subquery()
        )
        stmt = (
            select(PublicationORM, UserORM.username, criteria, questions)
            .join(UserORM, UserORM.id == PublicationORM.author_id)
            .where(PublicationORM.space_id == space_id)
            .order_by(PublicationORM.published_at.desc(), PublicationORM.id)
        )
        return [(r[0], r[1], r[2], r[3]) for r in (await self.db.execute(stmt)).all()]

    async def publication(
        self, space_id: str, publication_id: str
    ) -> tuple[PublicationORM, str] | None:
        stmt = (
            select(PublicationORM, UserORM.username)
            .join(UserORM, UserORM.id == PublicationORM.author_id)
            .where(PublicationORM.space_id == space_id, PublicationORM.id == publication_id)
        )
        row = (await self.db.execute(stmt)).first()
        return (row[0], row[1]) if row else None

    async def publication_criteria(self, publication_id: str) -> list[PublicationCriterionORM]:
        stmt = (
            select(PublicationCriterionORM)
            .where(PublicationCriterionORM.publication_id == publication_id)
            .order_by(PublicationCriterionORM.position, PublicationCriterionORM.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def publication_questions(self, publication_id: str) -> list[PublicationQuestionORM]:
        stmt = (
            select(PublicationQuestionORM)
            .where(PublicationQuestionORM.publication_id == publication_id)
            .order_by(PublicationQuestionORM.position, PublicationQuestionORM.id)
        )
        return list((await self.db.execute(stmt)).scalars())

    async def delete_member(self, space_id: str, user_id: str) -> None:
        await self.db.execute(
            delete(SpaceMemberORM).where(
                SpaceMemberORM.space_id == space_id, SpaceMemberORM.user_id == user_id
            )
        )
        await self.db.execute(
            delete(PublicationORM).where(
                PublicationORM.space_id == space_id, PublicationORM.author_id == user_id
            )
        )
