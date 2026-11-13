from sqlalchemy.ext.asyncio import AsyncSession

from models.protocol import AssignmentORM, CriterionORM
from repositories.protocol import ProtocolRepository

QUICK_CRITERION = "Оценка"


def quick_title(max_points: int, taken: bool = False) -> str:
    base = f"Быстрая оценка (0–{max_points})"
    return base + " [служебная]" if taken else base


class QuickAssignments:
    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ProtocolRepository(db)

    async def ensure(self, teacher_id: str, max_points: int) -> str:
        await self.repo.lock_teacher_assignments(teacher_id)
        titles = await self.repo.assignment_by_title(teacher_id, quick_title(max_points))
        if titles is not None and titles.is_system:
            return titles.id
        title = quick_title(max_points, taken=titles is not None)
        again = await self.repo.assignment_by_title(teacher_id, title)
        if again is not None:
            return again.id
        assignment = AssignmentORM(
            teacher_id=teacher_id,
            title=title,
            description="Служебная работа для режима быстрой оценки",
            questions_per_submission=1,
            is_system=True,
        )
        self.db.add(assignment)
        await self.db.flush()
        self.db.add(
            CriterionORM(
                assignment_id=assignment.id, name=QUICK_CRITERION, max_points=max_points, position=0
            )
        )
        await self.db.flush()
        return assignment.id
