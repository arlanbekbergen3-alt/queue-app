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


@pytest.mark.asyncio
async def test_not_ready_and_join_window_columns_default_safely_and_roll_back(migration_engine):
    await alembic_async("upgrade", "e5f6a7b8c9d0")
    async with migration_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO queue_sessions (id, teacher_id, title, code, status, next_ticket,"
                " avg_service_seconds, created_at) VALUES ('s1', 'u1', 'Lab', 'ABC234', 'open',"
                " 3, 300, now())"
            )
        )
        for n in (1, 2):
            await conn.execute(
                text(
                    "INSERT INTO entries (id, session_id, name, token_hash, ticket, sort_order,"
                    " status, joined_at) VALUES (:id, 's1', 'N', :th, :n, :n, 'waiting', now())"
                ),
                {"id": f"e{n}", "th": f"{n}" * 16, "n": n},
            )

    await alembic_async("upgrade", "f6a7b8c9d0e1")

    async with migration_engine.connect() as conn:
        entries = (await conn.execute(text("SELECT not_ready, not_ready_count FROM entries"))).all()
        join_open = (await conn.execute(text("SELECT join_open FROM queue_sessions"))).scalar_one()
    assert {(e.not_ready, e.not_ready_count) for e in entries} == {(False, 0)}
    assert join_open is True

    await alembic_async("downgrade", "e5f6a7b8c9d0")
    async with migration_engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name IN ('entries', 'queue_sessions')"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM entries"))).scalar_one()
    assert not {"not_ready", "not_ready_count", "join_open"} & set(columns)
    assert count == 2
    await alembic_async("upgrade", "head")


@pytest.mark.asyncio
async def test_student_history_migration_links_existing_entries_and_numbers_attempts(
    migration_engine,
):
    await alembic_async("upgrade", "f6a7b8c9d0e1")
    async with migration_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO assignments (id, teacher_id, title, description,"
                " questions_per_submission, practice_enabled, created_at)"
                " VALUES ('a1', 'u1', 'Lab', '', 3, false, now())"
            )
        )
        for sid, code, group in (("s1", "AAAAAA", "ИС-22"), ("s2", "BBBBBB", " ис-22 ")):
            await conn.execute(
                text(
                    "INSERT INTO queue_sessions (id, teacher_id, title, group_name, code, status,"
                    " next_ticket, avg_service_seconds, created_at, assignment_id)"
                    " VALUES (:id, 'u1', 'S', :g, :c, 'open', 5, 300, now(), 'a1')"
                ),
                {"id": sid, "g": group, "c": code},
            )
        rows = [
            ("e1", "s1", "Алёна Фёдорова", 1, "completed", 1),
            ("e2", "s2", "алена  федорова", 1, "completed", 2),
            ("e3", "s2", "Борис", 2, "completed", 3),
            ("e4", "s1", "Алена Федорова", 2, "cancelled", 4),
        ]
        for entry_id, session_id, name, ticket, status, minute in rows:
            await conn.execute(
                text(
                    "INSERT INTO entries (id, session_id, name, token_hash, ticket, sort_order,"
                    " status, joined_at) VALUES (:id, :s, :n, :th, :t, :t, 'done', now())"
                ),
                {"id": entry_id, "s": session_id, "n": name, "th": entry_id * 16, "t": ticket},
            )
            await conn.execute(
                text(
                    "INSERT INTO submissions (id, entry_id, session_id, assignment_id, status,"
                    " started_at, comment, selection_seed, pool_question_ids, created_at)"
                    " VALUES (:id, :e, :s, 'a1', :st, now() + make_interval(mins => :m), '',"
                    " 'seed', '[]', now())"
                ),
                {"id": f"sub{entry_id}", "e": entry_id, "s": session_id, "st": status, "m": minute},
            )

    await alembic_async("upgrade", "a7b8c9d0e1f2")

    async with migration_engine.connect() as conn:
        students = (
            await conn.execute(
                text("SELECT id, name_key, group_key FROM students ORDER BY name_key")
            )
        ).all()
        linked = (
            await conn.execute(text("SELECT e.id, e.student_id FROM entries e ORDER BY e.id"))
        ).all()
        attempts = (
            await conn.execute(text("SELECT id, attempt_number FROM submissions ORDER BY id"))
        ).all()
    assert [(s.name_key, s.group_key) for s in students] == [
        ("алена" + "федорова", "ис-22"),
        ("борис", "ис-22"),
    ]
    by_entry = {row.id: row.student_id for row in linked}
    assert by_entry["e1"] == by_entry["e2"] == by_entry["e4"] != by_entry["e3"]
    numbers = {row.id: row.attempt_number for row in attempts}
    assert (numbers["sube1"], numbers["sube2"], numbers["sube3"]) == (1, 2, 1)

    await alembic_async("downgrade", "f6a7b8c9d0e1")
    async with migration_engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name = 'students'"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM submissions"))).scalar_one()
    assert tables == [] and count == 4
    await alembic_async("upgrade", "head")


@pytest.mark.asyncio
async def test_template_flag_defaults_to_false_for_existing_works_and_rolls_back(migration_engine):
    await alembic_async("upgrade", "a7b8c9d0e1f2")
    async with migration_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO assignments (id, teacher_id, title, description,"
                " questions_per_submission, practice_enabled, created_at)"
                " VALUES ('a1', 'u1', 'Lab', '', 3, false, now())"
            )
        )

    await alembic_async("upgrade", "b8c9d0e1f2a3")
    async with migration_engine.connect() as conn:
        flags = (await conn.execute(text("SELECT is_template FROM assignments"))).scalars().all()
    assert flags == [False]

    await alembic_async("downgrade", "a7b8c9d0e1f2")
    async with migration_engine.connect() as conn:
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'assignments'"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM assignments"))).scalar_one()
    assert "is_template" not in columns and count == 1
    await alembic_async("upgrade", "head")


@pytest.mark.asyncio
async def test_subject_spaces_migration_adds_tables_and_origin_columns_and_rolls_back(
    migration_engine,
):
    await alembic_async("upgrade", "b8c9d0e1f2a3")
    async with migration_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO assignments (id, teacher_id, title, description,"
                " questions_per_submission, practice_enabled, is_template, created_at)"
                " VALUES ('a1', 'u1', 'Lab', '', 3, false, false, now())"
            )
        )

    await alembic_async("upgrade", "c9d0e1f2a3b4")
    async with migration_engine.begin() as conn:
        origin = (
            await conn.execute(
                text("SELECT origin_author, origin_space, origin_title FROM assignments")
            )
        ).one()
        await conn.execute(
            text(
                "INSERT INTO subject_spaces (id, name, invite_code, owner_id, created_at)"
                " VALUES ('s1', 'Группа', 'ABCDEFGH23', 'u1', now())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO space_members (id, space_id, user_id, joined_at)"
                " VALUES ('m1', 's1', 'u1', now())"
            )
        )
    assert tuple(origin) == (None, None, None)

    await alembic_async("downgrade", "b8c9d0e1f2a3")
    async with migration_engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables"
                        " WHERE table_name LIKE 'space%' OR table_name = 'subject_spaces'"
                    )
                )
            )
            .scalars()
            .all()
        )
        columns = (
            (
                await conn.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns"
                        " WHERE table_name = 'assignments'"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM assignments"))).scalar_one()
    assert tables == [] and count == 1
    assert not {"origin_author", "origin_space", "origin_title"} & set(columns)
    await alembic_async("upgrade", "head")


@pytest.mark.asyncio
async def test_order_mode_migration_defaults_existing_sessions_and_protects_the_journal(
    migration_engine,
):
    await alembic_async("upgrade", "c9d0e1f2a3b4")
    async with migration_engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO users (id, username, password_hash) VALUES ('u1', 't', 'x')")
        )
        await conn.execute(
            text(
                "INSERT INTO queue_sessions (id, teacher_id, title, code, status, next_ticket,"
                " avg_service_seconds, created_at) VALUES ('s1', 'u1', 'Lab', 'ABC234', 'open',"
                " 3, 300, now())"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO entries (id, session_id, name, token_hash, ticket, sort_order,"
                " status, joined_at) VALUES ('e1', 's1', 'N', :th, 1, 1, 'waiting', now())"
            ),
            {"th": "1" * 16},
        )

    await alembic_async("upgrade", "d0e1f2a3b4c5")

    async with migration_engine.connect() as conn:
        session = (
            await conn.execute(
                text(
                    "SELECT order_mode, ready_priority, ready_bonus_points, ready_skip_questions,"
                    " lottery_at FROM queue_sessions"
                )
            )
        ).one()
        ready = (await conn.execute(text("SELECT ready_now FROM entries"))).scalar_one()
    assert tuple(session) == ("join", False, 0, 0, None)
    assert ready is False

    async with migration_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO session_events (id, occurred_at, session_id, actor_id, kind, details)"
                " VALUES ('x1', now(), 's1', 'u1', 'lottery', '{}')"
            )
        )
    with pytest.raises(Exception, match="append-only"):
        async with migration_engine.begin() as conn:
            await conn.execute(text("UPDATE session_events SET kind = 'y'"))
    with pytest.raises(IntegrityError):
        async with migration_engine.begin() as conn:
            await conn.execute(text("UPDATE queue_sessions SET ready_bonus_points = 6"))

    await alembic_async("downgrade", "c9d0e1f2a3b4")
    async with migration_engine.connect() as conn:
        tables = (
            (
                await conn.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables WHERE table_name = 'session_events'"
                    )
                )
            )
            .scalars()
            .all()
        )
        count = (await conn.execute(text("SELECT count(*) FROM entries"))).scalar_one()
    assert tables == [] and count == 1
    await alembic_async("upgrade", "head")
