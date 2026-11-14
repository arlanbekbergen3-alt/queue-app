from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import ConflictError, NotFoundError
from models.protocol import AssignmentORM, CriterionORM, QuestionORM
from repositories.protocol import ProtocolRepository
from schemas.protocol import AssignmentOut
from services.protocol import ProtocolService

TITLE_MAX = 120


def fit_title(source: str, suffix: str) -> str:
    return source[: TITLE_MAX - len(suffix)].rstrip() + suffix


def copy_title(source: str, taken: set[str]) -> str:
    candidate = fit_title(source, " (копия)")
    number = 2
    while candidate in taken:
        candidate = fit_title(source, f" (копия {number})")
        number += 1
    return candidate


class AssignmentCopyService:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProtocolRepository(db)
        self.protocol = ProtocolService(db)

    async def copy(
        self, teacher_id: str, source_id: str, title: str | None, *, template_only: bool = False
    ) -> AssignmentOut:
        await self.repo.lock_teacher_assignments(teacher_id)
        source = await self.repo.get_assignment(source_id, teacher_id, lock=True)
        if source is not None and source.is_system:
            source = None
        if source is None or (template_only and not source.is_template):
            raise NotFoundError("Работа не найдена", code="assignment_not_found")
        if await self.repo.count_assignments(teacher_id) >= settings.MAX_ASSIGNMENTS_PER_TEACHER:
            raise ConflictError("Достигнут предел числа работ", code="assignment_limit")
        taken = await self.repo.assignment_titles(teacher_id)
        if title is None:
            title = copy_title(source.title, taken)
        elif title in taken:
            raise ConflictError("Работа с таким названием уже есть", code="assignment_title_taken")
        clone = AssignmentORM(
            teacher_id=teacher_id,
            title=title,
            description=source.description,
            questions_per_submission=source.questions_per_submission,
            practice_enabled=source.practice_enabled,
            require_extreme_reason=source.require_extreme_reason,
            deviation_warning=source.deviation_warning,
            deviation_sigma=source.deviation_sigma,
            is_template=False,
        )
        self.db.add(clone)
        await self.db.flush()
        clone_id = clone.id
        for criterion in await self.repo.list_criteria(source.id):
            self.db.add(
                CriterionORM(
                    assignment_id=clone_id,
                    name=criterion.name,
                    max_points=criterion.max_points,
                    position=criterion.position,
                    levels=criterion.levels,
                )
            )
        for question in await self.repo.list_questions(source.id):
            self.db.add(
                QuestionORM(
                    assignment_id=clone_id,
                    text=question.text,
                    difficulty=question.difficulty,
                    topic=question.topic,
                    position=question.position,
                )
            )
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise ConflictError(
                "Работа с таким названием уже есть", code="assignment_title_taken"
            ) from None
        return await self.protocol.get_assignment(teacher_id, clone_id)

    async def templates(self, teacher_id: str) -> list[AssignmentOut]:
        found = await self.repo.list_templates(teacher_id)
        return [await self.protocol._assignment_out(a) for a in found]
