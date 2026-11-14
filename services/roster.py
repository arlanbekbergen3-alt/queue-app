import re
import unicodedata

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.exceptions import ConflictError, NotFoundError, UnprocessableError
from models.entry import EntryORM
from models.roster import RosterEntryORM
from schemas.roster import PublicRosterOut, RosterGroupOut, RosterOut, RosterSkippedOut
from services.identity import group_key, name_key

_PREFIX = re.compile(r"^\s*(?:\(?\d{1,4}[.)\]:]|\(\d{1,4}\)|\d{1,4}\s*[-–—]|[-–—•·*▪►>+])(?:\s+|$)")


def clean_group(value: str) -> str:
    group = " ".join(value.split())
    if (
        not group
        or len(group) > 60
        or any(unicodedata.category(ch) in ("Cc", "Cf") for ch in group)
    ):
        raise UnprocessableError("Название группы: от 1 до 60 символов", code="invalid_group")
    return group


def clean_name(raw: str) -> str:
    line = raw.replace(" ", " ").strip()
    previous = None
    while previous != line:
        previous = line
        line = _PREFIX.sub("", line, count=1).strip()
    return " ".join(line.split())


def parse_names(text: str, limit: int) -> tuple[list[str], list[RosterSkippedOut]]:
    names: list[str] = []
    skipped: list[RosterSkippedOut] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        name = clean_name(raw)
        if not name:
            continue
        if any(unicodedata.category(ch) in ("Cc", "Cf") for ch in name):
            skipped.append(RosterSkippedOut(text=name[:60], reason="control"))
        elif len(name) > settings.NAME_MAX_LENGTH:
            skipped.append(RosterSkippedOut(text=name[:60], reason="too_long"))
        elif name_key(name) in seen:
            skipped.append(RosterSkippedOut(text=name, reason="duplicate"))
        elif len(names) >= limit:
            skipped.append(RosterSkippedOut(text=name, reason="over_limit"))
        else:
            seen.add(name_key(name))
            names.append(name)
    return names, skipped


class RosterService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def groups(self, teacher_id: str) -> list[RosterGroupOut]:
        rows = await self.db.execute(
            select(RosterEntryORM.group_name, func.count())
            .where(RosterEntryORM.teacher_id == teacher_id)
            .group_by(RosterEntryORM.group_key, RosterEntryORM.group_name)
            .order_by(RosterEntryORM.group_name)
        )
        return [RosterGroupOut(group=g, count=n) for g, n in rows.all()]

    async def names(self, teacher_id: str, group: str) -> list[str]:
        rows = await self.db.execute(
            select(RosterEntryORM.name)
            .where(
                RosterEntryORM.teacher_id == teacher_id,
                RosterEntryORM.group_key == group_key(group),
            )
            .order_by(RosterEntryORM.name_key)
        )
        return list(rows.scalars())

    async def get(self, teacher_id: str, group: str) -> RosterOut:
        group = clean_group(group)
        names = await self.names(teacher_id, group)
        if not names:
            raise NotFoundError("Список группы не найден", code="roster_not_found")
        return RosterOut(group=group, names=names)

    async def replace(self, teacher_id: str, group: str, text: str) -> RosterOut:
        group = clean_group(group)
        key = group_key(group)
        names, skipped = parse_names(text, settings.MAX_ROSTER_PER_GROUP)
        if not names:
            raise UnprocessableError("В списке нет ни одной фамилии", code="roster_empty")
        await self.db.execute(
            select(func.pg_advisory_xact_lock(func.hashtext("roster:" + teacher_id)))
        )
        known = await self.db.execute(
            select(func.count(func.distinct(RosterEntryORM.group_key))).where(
                RosterEntryORM.teacher_id == teacher_id, RosterEntryORM.group_key != key
            )
        )
        if known.scalar_one() >= settings.MAX_ROSTER_GROUPS:
            raise ConflictError("Слишком много списков групп", code="roster_groups_limit")
        await self.db.execute(
            delete(RosterEntryORM).where(
                RosterEntryORM.teacher_id == teacher_id, RosterEntryORM.group_key == key
            )
        )
        for name in names:
            self.db.add(
                RosterEntryORM(
                    teacher_id=teacher_id,
                    group_key=key,
                    group_name=group,
                    name_key=name_key(name),
                    name=name,
                )
            )
        await self.db.commit()
        return RosterOut(group=group, names=sorted(names, key=name_key), skipped=skipped)

    async def delete(self, teacher_id: str, group: str) -> None:
        key = group_key(clean_group(group))
        result = await self.db.execute(
            delete(RosterEntryORM)
            .where(RosterEntryORM.teacher_id == teacher_id, RosterEntryORM.group_key == key)
            .returning(RosterEntryORM.id)
        )
        if not result.all():
            raise NotFoundError("Список группы не найден", code="roster_not_found")
        await self.db.commit()

    async def canonical(self, teacher_id: str, group: str | None, name: str) -> str | None:
        if not group:
            return None
        found = await self.db.execute(
            select(RosterEntryORM.name).where(
                RosterEntryORM.teacher_id == teacher_id,
                RosterEntryORM.group_key == group_key(group),
                RosterEntryORM.name_key == name_key(name),
            )
        )
        return found.scalar_one_or_none()

    async def has_roster(self, teacher_id: str, group: str | None) -> bool:
        if not group:
            return False
        found = await self.db.execute(
            select(RosterEntryORM.id)
            .where(
                RosterEntryORM.teacher_id == teacher_id,
                RosterEntryORM.group_key == group_key(group),
            )
            .limit(1)
        )
        return found.scalar_one_or_none() is not None

    async def public(
        self, session_id: str, teacher_id: str, group: str | None, only: bool
    ) -> PublicRosterOut:
        names = await self.names(teacher_id, group) if group else []
        if not names:
            return PublicRosterOut(names=[], only=only)
        taken = await self.db.execute(
            select(EntryORM.name).where(
                EntryORM.session_id == session_id, EntryORM.status != "left"
            )
        )
        gone = {name_key(n) for n in taken.scalars()}
        return PublicRosterOut(names=[n for n in names if name_key(n) not in gone], only=only)
