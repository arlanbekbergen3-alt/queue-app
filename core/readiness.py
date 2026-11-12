from functools import cache
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent


@cache
def expected_revision() -> str:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    return ScriptDirectory.from_config(config).get_current_head() or ""


async def schema_is_current(db: AsyncSession) -> bool:
    row = (await db.execute(text("SELECT version_num FROM alembic_version"))).scalars().all()
    return row == [expected_revision()]
