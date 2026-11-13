import secrets

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import ConflictError, ForbiddenError, NotFoundError, UnprocessableError
from models.protocol import AssignmentORM, CriterionORM, QuestionORM
from models.space import (
    PublicationCriterionORM,
    PublicationORM,
    PublicationQuestionORM,
    SpaceMemberORM,
    SubjectSpaceORM,
)
from models.user import UserORM
from repositories.protocol import ProtocolRepository
from repositories.spaces import SpaceRepository
from schemas.protocol import AssignmentOut
from schemas.spaces import (
    MemberOut,
    PubCriterionOut,
    PublicationDetailOut,
    PublicationOut,
    PubQuestionOut,
    SpaceDetailOut,
    SpaceOut,
)
from services.assignment_copy import copy_title
from services.protocol import ProtocolService
from services.queue import CODE_ALPHABET

INVITE_LENGTH = 10


def new_invite_code() -> str:
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(INVITE_LENGTH))


def normalize_invite(code: str) -> str:
    return code.strip().upper()


class SpaceService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = SpaceRepository(db)
        self.protocol_repo = ProtocolRepository(db)
        self.protocol = ProtocolService(db)

    async def _space(
        self, user: UserORM, space_id: str, *, lock: bool = False, owner: bool = False
    ) -> SubjectSpaceORM:
        space = await self.repo.get_space(space_id)
        if space is None or await self.repo.membership(space_id, user.id) is None:
            raise NotFoundError("Группа не найдена", code="space_not_found")
        if lock:
            space = await self.repo.get_space(space_id, lock=True)
            if space is None or await self.repo.membership(space_id, user.id) is None:
                raise NotFoundError("Группа не найдена", code="space_not_found")
        if owner and space.owner_id != user.id:
            raise ForbiddenError("Это может только владелец группы", code="not_space_owner")
        return space

    async def _out(self, space: SubjectSpaceORM, user_id: str) -> SpaceOut:
        is_owner = space.owner_id == user_id
        return SpaceOut(
            id=space.id,
            name=space.name,
            role="owner" if is_owner else "member",
            members_count=await self.repo.count_members(space.id),
            publications_count=await self.repo.count_publications(space.id),
            invite_code=space.invite_code if is_owner else None,
            created_at=space.created_at,
        )

    async def _check_membership_limit(self, user_id: str) -> None:
        if await self.repo.count_memberships(user_id) >= settings.MAX_SPACES_JOINED:
            raise ConflictError("Вы состоите в слишком многих группах", code="space_limit")

    async def create(self, user: UserORM, name: str) -> SpaceOut:
        await self.repo.lock_user(user.id)
        if await self.repo.count_owned(user.id) >= settings.MAX_SPACES_OWNED:
            raise ConflictError("Достигнут предел числа ваших групп", code="space_limit")
        await self._check_membership_limit(user.id)
        code = new_invite_code()
        while await self.repo.space_by_code(code) is not None:
            code = new_invite_code()
        space = SubjectSpaceORM(name=name, invite_code=code, owner_id=user.id)
        self.db.add(space)
        await self.db.flush()
        space_id = space.id
        self.db.add(SpaceMemberORM(space_id=space_id, user_id=user.id))
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "Не удалось создать группу, повторите", code="space_conflict"
            ) from None
        created = await self.repo.get_space(space_id)
        assert created is not None
        return await self._out(created, user.id)

    async def list_for(self, user: UserORM) -> list[SpaceOut]:
        return [await self._out(s, user.id) for s in await self.repo.spaces_of(user.id)]

    async def detail(self, user: UserORM, space_id: str) -> SpaceDetailOut:
        space = await self._space(user, space_id)
        base = await self._out(space, user.id)
        members = [
            MemberOut(
                user_id=m.user_id,
                username=username,
                is_owner=m.user_id == space.owner_id,
                joined_at=m.joined_at,
            )
            for m, username in await self.repo.members(space_id)
        ]
        return SpaceDetailOut(**base.model_dump(), members=members)

    async def join(self, user: UserORM, code: str) -> SpaceOut:
        await self.repo.lock_user(user.id)
        space = await self.repo.space_by_code(normalize_invite(code))
        if space is None:
            raise NotFoundError("Неверный код приглашения", code="space_code_invalid")
        space_id = space.id
        locked = await self.repo.get_space(space_id, lock=True)
        if locked is None:
            raise NotFoundError("Неверный код приглашения", code="space_code_invalid")
        if locked.invite_code != normalize_invite(code):
            raise NotFoundError("Неверный код приглашения", code="space_code_invalid")
        if await self.repo.membership(space_id, user.id) is None:
            await self._check_membership_limit(user.id)
            if await self.repo.count_members(space_id) >= settings.MAX_SPACE_MEMBERS:
                raise ConflictError("В группе нет свободных мест", code="space_full")
            self.db.add(SpaceMemberORM(space_id=space_id, user_id=user.id))
            await self.db.commit()
        refreshed = await self.repo.get_space(space_id)
        assert refreshed is not None
        return await self._out(refreshed, user.id)

    async def regenerate_invite(self, user: UserORM, space_id: str) -> SpaceOut:
        space = await self._space(user, space_id, lock=True, owner=True)
        code = new_invite_code()
        while await self.repo.space_by_code(code) is not None:
            code = new_invite_code()
        space.invite_code = code
        await self.db.commit()
        return await self._out(space, user.id)

    async def leave(self, user: UserORM, space_id: str) -> None:
        space = await self._space(user, space_id, lock=True)
        if space.owner_id == user.id:
            raise ConflictError(
                "Владелец не может выйти: удалите группу", code="owner_cannot_leave"
            )
        await self.repo.delete_member(space_id, user.id)
        await self.db.commit()

    async def remove_member(self, user: UserORM, space_id: str, member_id: str) -> None:
        space = await self._space(user, space_id, lock=True, owner=True)
        if member_id == space.owner_id:
            raise ConflictError("Владельца нельзя удалить из группы", code="owner_cannot_leave")
        if await self.repo.membership(space_id, member_id) is None:
            raise NotFoundError("Участник не найден", code="member_not_found")
        await self.repo.delete_member(space_id, member_id)
        await self.db.commit()

    async def delete(self, user: UserORM, space_id: str) -> None:
        space = await self._space(user, space_id, lock=True, owner=True)
        await self.db.delete(space)
        await self.db.commit()

    async def publish(
        self, user: UserORM, space_id: str, assignment_id: str, title: str | None
    ) -> PublicationDetailOut:
        await self._space(user, space_id, lock=True)
        assignment = await self.protocol_repo.get_assignment(assignment_id, user.id)
        if assignment is not None and assignment.is_system:
            assignment = None
        if assignment is None:
            raise NotFoundError("Работа не найдена", code="assignment_not_found")
        criteria = await self.protocol_repo.list_criteria(assignment.id)
        questions = await self.protocol_repo.list_questions(assignment.id)
        if not criteria and not questions:
            raise UnprocessableError(
                "Нечего публиковать: у работы нет критериев и вопросов", code="publication_empty"
            )
        if await self.repo.count_publications(space_id) >= settings.MAX_SPACE_PUBLICATIONS:
            raise ConflictError("В группе слишком много материалов", code="publication_limit")
        if (
            await self.repo.count_publications(space_id, user.id)
            >= settings.MAX_PUBLICATIONS_PER_AUTHOR
        ):
            raise ConflictError(
                "Вы опубликовали слишком много материалов", code="publication_limit"
            )
        publication = PublicationORM(
            space_id=space_id, author_id=user.id, title=title or assignment.title
        )
        self.db.add(publication)
        await self.db.flush()
        publication_id = publication.id
        for c in criteria:
            self.db.add(
                PublicationCriterionORM(
                    publication_id=publication_id,
                    name=c.name,
                    max_points=c.max_points,
                    position=c.position,
                )
            )
        for q in questions:
            self.db.add(
                PublicationQuestionORM(
                    publication_id=publication_id,
                    text=q.text,
                    difficulty=q.difficulty,
                    topic=q.topic,
                    position=q.position,
                )
            )
        await self.db.commit()
        return await self.publication(user, space_id, publication_id)

    async def list_publications(self, user: UserORM, space_id: str) -> list[PublicationOut]:
        await self._space(user, space_id)
        return [
            PublicationOut(
                id=p.id,
                title=p.title,
                author=author,
                mine=p.author_id == user.id,
                published_at=p.published_at,
                criteria_count=criteria,
                questions_count=questions,
            )
            for p, author, criteria, questions in await self.repo.publications(space_id)
        ]

    async def publication(
        self, user: UserORM, space_id: str, publication_id: str
    ) -> PublicationDetailOut:
        await self._space(user, space_id)
        found = await self.repo.publication(space_id, publication_id)
        if found is None:
            raise NotFoundError("Материал не найден", code="publication_not_found")
        p, author = found
        criteria = await self.repo.publication_criteria(p.id)
        questions = await self.repo.publication_questions(p.id)
        return PublicationDetailOut(
            id=p.id,
            title=p.title,
            author=author,
            mine=p.author_id == user.id,
            published_at=p.published_at,
            criteria_count=len(criteria),
            questions_count=len(questions),
            criteria=[PubCriterionOut.model_validate(c) for c in criteria],
            questions=[PubQuestionOut.model_validate(q) for q in questions],
        )

    async def unpublish(self, user: UserORM, space_id: str, publication_id: str) -> None:
        await self._space(user, space_id, lock=True)
        found = await self.repo.publication(space_id, publication_id)
        if found is None:
            raise NotFoundError("Материал не найден", code="publication_not_found")
        if found[0].author_id != user.id:
            raise ForbiddenError(
                "Снять с публикации может только автор", code="not_publication_author"
            )
        await self.db.delete(found[0])
        await self.db.commit()

    async def copy(
        self, user: UserORM, space_id: str, publication_id: str, title: str | None
    ) -> AssignmentOut:
        await self.protocol_repo.lock_teacher_assignments(user.id)
        space = await self._space(user, space_id, lock=True)
        found = await self.repo.publication(space_id, publication_id)
        if found is None:
            raise NotFoundError("Материал не найден", code="publication_not_found")
        source, author = found
        if (
            await self.protocol_repo.count_assignments(user.id)
            >= settings.MAX_ASSIGNMENTS_PER_TEACHER
        ):
            raise ConflictError("Достигнут предел числа работ", code="assignment_limit")
        taken = await self.protocol_repo.assignment_titles(user.id)
        if title is None:
            title = copy_title(source.title, taken)
        elif title in taken:
            raise ConflictError("Работа с таким названием уже есть", code="assignment_title_taken")
        clone = AssignmentORM(
            teacher_id=user.id,
            title=title,
            origin_author=author,
            origin_space=space.name,
            origin_title=source.title,
        )
        self.db.add(clone)
        await self.db.flush()
        clone_id = clone.id
        for c in await self.repo.publication_criteria(source.id):
            self.db.add(
                CriterionORM(
                    assignment_id=clone_id,
                    name=c.name,
                    max_points=c.max_points,
                    position=c.position,
                )
            )
        for q in await self.repo.publication_questions(source.id):
            self.db.add(
                QuestionORM(
                    assignment_id=clone_id,
                    text=q.text,
                    difficulty=q.difficulty,
                    topic=q.topic,
                    position=q.position,
                )
            )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "Работа с таким названием уже есть", code="assignment_title_taken"
            ) from None
        return await self.protocol.get_assignment(user.id, clone_id)
