import unicodedata

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models.student import StudentORM

KEY_LENGTH = 60


def _key(value: str | None) -> str:
    if not value:
        return ""
    folded = unicodedata.normalize("NFKC", value).lower().replace("ё", "е")
    return "".join(folded.split())[:KEY_LENGTH]


def name_key(name: str) -> str:
    return _key(name)


def group_key(group: str | None) -> str:
    return _key(group)


async def resolve_student(db: AsyncSession, student: StudentORM) -> StudentORM:
    seen: set[str] = set()
    while student.merged_into_id is not None and student.id not in seen:
        seen.add(student.id)
        target = await db.get(StudentORM, student.merged_into_id)
        if target is None:
            break
        student = target
    return student


async def find_or_create_student(
    db: AsyncSession, teacher_id: str, group: str | None, name: str
) -> StudentORM:
    gkey, nkey = group_key(group), name_key(name)
    lookup = select(StudentORM).where(
        StudentORM.teacher_id == teacher_id,
        StudentORM.group_key == gkey,
        StudentORM.name_key == nkey,
        StudentORM.auto_link.is_(True),
    )
    found = (await db.execute(lookup)).scalar_one_or_none()
    if found is None:
        found = (
            await db.execute(
                pg_insert(StudentORM)
                .values(
                    teacher_id=teacher_id,
                    group_key=gkey,
                    name_key=nkey,
                    display_name=name,
                    group_name=group,
                    auto_link=True,
                )
                .on_conflict_do_nothing(
                    index_elements=["teacher_id", "group_key", "name_key"],
                    index_where=text("auto_link"),
                )
                .returning(StudentORM)
            )
        ).scalar_one_or_none()
    if found is None:
        found = (await db.execute(lookup.execution_options(populate_existing=True))).scalar_one()
    return await resolve_student(db, found)
