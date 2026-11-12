import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TEST_DATABASE_URL

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_DB = "queue_migration_test"
URL = TEST_DATABASE_URL.set(database=MIGRATION_DB)


def alembic(*args: str) -> None:
    env = {**os.environ, "DATABASE_URL": URL.render_as_string(hide_password=False)}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def alembic_async(*args: str) -> None:
    await asyncio.to_thread(alembic, *args)


@pytest_asyncio.fixture
async def migration_engine():
    admin = create_async_engine(
        TEST_DATABASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
        await conn.execute(text(f'CREATE DATABASE "{MIGRATION_DB}"'))
    engine = create_async_engine(URL, poolclass=NullPool)
    yield engine
    await engine.dispose()
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{MIGRATION_DB}" WITH (FORCE)'))
    await admin.dispose()


async def seed_old_schema(engine):
    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO queue_sessions (id, teacher_id, title, code, status, next_ticket,"
                " avg_service_seconds, created_at) VALUES ('s1', 'u1', 'Lab', 'ABC234', 'open',"
                " 6, 300, now())"
            )
        )
        rows = [
            ("e1", "A", 1, "done"),
            ("e2", "B", 2, "called"),
            ("e3", "C", 3, "waiting"),
            ("e4", "D", 0, "waiting"),
            ("e5", "E", -1, "waiting"),
            ("e6", "F", 5, "skipped"),
        ]
        for entry_id, name, ticket, status in rows:
            await conn.execute(
                text(
                    "INSERT INTO entries (id, session_id, name, token_hash, ticket, status,"
                    " joined_at) VALUES (:id, 's1', :name, :th, :ticket, :status, now())"
                ),
                {
                    "id": entry_id,
                    "name": name,
                    "th": entry_id * 16,
                    "ticket": ticket,
                    "status": status,
                },
            )


@pytest.mark.asyncio
async def test_migration_keeps_existing_entries_and_fills_sort_order(migration_engine):
    await alembic_async("upgrade", "a1b2c3d4e5f6")
    await seed_old_schema(migration_engine)

    await alembic_async("upgrade", "head")

    async with migration_engine.connect() as conn:
        rows = (
            await conn.execute(text("SELECT name, ticket, sort_order FROM entries ORDER BY name"))
        ).all()
    assert [(r.name, r.ticket, r.sort_order) for r in rows] == [
        ("A", 1, 1),
        ("B", 2, 2),
        ("C", 3, 3),
        ("D", 0, 0),
        ("E", -1, -1),
        ("F", 5, 5),
    ]
    async with migration_engine.connect() as conn:
        order = (
            (
                await conn.execute(
                    text("SELECT name FROM entries WHERE status = 'waiting' ORDER BY sort_order")
                )
            )
            .scalars()
            .all()
        )
    assert order == ["E", "D", "C"]


@pytest.mark.asyncio
async def test_migration_adds_waiting_uniqueness_and_downgrades_cleanly(migration_engine):
    await alembic_async("upgrade", "a1b2c3d4e5f6")
    await seed_old_schema(migration_engine)
    await alembic_async("upgrade", "head")

    with pytest.raises(IntegrityError):
        async with migration_engine.begin() as conn:
            await conn.execute(text("UPDATE entries SET sort_order = 3 WHERE id = 'e4'"))

    await alembic_async("downgrade", "a1b2c3d4e5f6")
    async with migration_engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'entries'"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM entries"))).scalar_one()
    assert "sort_order" not in columns
    assert count == 6
    await alembic_async("upgrade", "head")


@pytest.mark.asyncio
async def test_group_column_is_added_nullable_and_existing_sessions_keep_working(migration_engine):
    await alembic_async("upgrade", "a1b2c3d4e5f6")
    await seed_old_schema(migration_engine)

    await alembic_async("upgrade", "head")

    async with migration_engine.begin() as conn:
        row = (
            await conn.execute(text("SELECT title, group_name FROM queue_sessions WHERE id = 's1'"))
        ).one()
        await conn.execute(text("UPDATE queue_sessions SET group_name = 'ИС-22' WHERE id = 's1'"))
    assert (row.title, row.group_name) == ("Lab", None)

    await alembic_async("downgrade", "b2c3d4e5f6a7")
    async with migration_engine.connect() as conn:
        count = (await conn.execute(text("SELECT count(*) FROM queue_sessions"))).scalar_one()
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'queue_sessions'"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert count == 1
    assert "group_name" not in columns
