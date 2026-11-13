import asyncio
from datetime import datetime
from uuid import uuid4

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

import services.retention as retention
from core.config import settings
from services.retention import PurgeResult, main, purge_closed_sessions, retention_loop
from tests.conftest import TEST_DATABASE_URL, TestingSessionLocal

API = "/api/v1"
S = f"{API}/sessions"


@pytest_asyncio.fixture
async def world(client, make_teacher, db_session):
    class World:
        pass

    w = World()
    w.client = client
    w.db = db_session
    w.headers = await make_teacher(f"ret_{datetime.now().strftime('%H%M%S%f')}")

    async def graded_session(title, students=("Анна", "Борис"), group="ИС-22"):
        session = (
            await client.post(
                S, headers=w.headers, json={"title": title, "group": group, "session_mode": "quick"}
            )
        ).json()
        code = session["code"]
        criteria = (
            await client.get(
                f"{API}/assignments/{session['assignment_id']}/criteria", headers=w.headers
            )
        ).json()
        for name in students:
            token = uuid4().hex
            await client.post(f"{S}/{code}/join", json={"name": name, "token": token})
            await client.post(f"{S}/{code}/next", headers=w.headers)
            current = (
                await client.get(f"{S}/{code}/submissions/current", headers=w.headers)
            ).json()
            await client.post(
                f"{API}/submissions/{current['id']}/complete",
                headers=w.headers,
                json={
                    "scores": [{"criterion_id": criteria[0]["id"], "points": 7}],
                    "comment": "ок",
                },
            )
        return session

    async def age(code, days, status="closed"):
        await w.db.execute(
            text(
                "UPDATE queue_sessions SET status = :s, closed_at = now() - make_interval(days => :d)"
                " WHERE code = :c"
            ),
            {"s": status, "d": days, "c": code},
        )
        await w.db.commit()

    async def count(table, where="true", **params):
        return (
            await w.db.execute(text(f"SELECT count(*) FROM {table} WHERE {where}"), params)
        ).scalar_one()

    w.graded_session = graded_session
    w.age = age
    w.count = count
    return w


@pytest.mark.asyncio
async def test_an_old_closed_session_disappears_with_everything_attached_to_it(world):
    session = await world.graded_session("Старая")
    sid = (
        await world.db.execute(
            text("SELECT id FROM queue_sessions WHERE code = :c"), {"c": session["code"]}
        )
    ).scalar_one()
    await world.age(session["code"], 200)
    assert await world.count("entries", "session_id = :i", i=sid) == 2
    result = await purge_closed_sessions(TestingSessionLocal, 180)
    assert result.sessions >= 1
    assert await world.count("queue_sessions", "id = :i", i=sid) == 0
    assert await world.count("entries", "session_id = :i", i=sid) == 0
    assert await world.count("submissions", "session_id = :i", i=sid) == 0
    leftovers = await world.count(
        "submission_scores", "submission_id NOT IN (SELECT id FROM submissions)"
    )
    assert leftovers == 0


@pytest.mark.asyncio
async def test_recent_closed_and_old_open_sessions_are_kept(world):
    recent = await world.graded_session("Недавняя")
    await world.age(recent["code"], 10)
    forgotten = await world.graded_session("Открытая")
    await world.db.execute(
        text("UPDATE queue_sessions SET created_at = now() - interval '400 days' WHERE code = :c"),
        {"c": forgotten["code"]},
    )
    await world.db.commit()
    edge = await world.graded_session("Почти")
    await world.age(edge["code"], 179)
    await purge_closed_sessions(TestingSessionLocal, 180)
    for session in (recent, forgotten, edge):
        assert await world.count("queue_sessions", "code = :c", c=session["code"]) == 1


@pytest.mark.asyncio
async def test_student_history_survives_in_newer_sessions_and_unused_records_go(world):
    old = await world.graded_session("Осень", students=("Анна Ким", "Вера Ли"))
    await world.graded_session("Весна", students=("Анна Ким",))
    await world.age(old["code"], 300)
    await world.db.execute(text("UPDATE students SET created_at = now() - interval '300 days'"))
    await world.db.commit()
    result = await purge_closed_sessions(TestingSessionLocal, 180)
    assert result.students >= 1
    students = (await world.client.get(f"{API}/students", headers=world.headers)).json()
    names = {s["name"] for s in students}
    assert "Анна Ким" in names and "Вера Ли" not in names
    anna = next(s for s in students if s["name"] == "Анна Ким")
    card = (await world.client.get(f"{API}/students/{anna['id']}", headers=world.headers)).json()
    assert sum(len(a["attempts"]) for a in card["assignments"]) == 1


@pytest.mark.asyncio
async def test_the_audit_journal_and_the_works_are_not_touched(world):
    session = await world.graded_session("Журнал")
    await world.client.get(f"{S}/{session['code']}/events-log", headers=world.headers)
    audit_before = await world.count("audit_log")
    events_before = await world.count("session_events")
    assignments_before = await world.count("assignments")
    assert audit_before > 0
    await world.age(session["code"], 400)
    await purge_closed_sessions(TestingSessionLocal, 180)
    assert await world.count("audit_log") == audit_before
    assert await world.count("session_events") == events_before
    assert await world.count("assignments") == assignments_before


@pytest.mark.asyncio
async def test_a_dry_run_counts_but_deletes_nothing(world):
    session = await world.graded_session("Пробный")
    await world.age(session["code"], 500)
    before = await world.count("queue_sessions")
    result = await purge_closed_sessions(TestingSessionLocal, 180, dry_run=True)
    assert result.sessions >= 1
    assert await world.count("queue_sessions") == before


@pytest.mark.asyncio
async def test_many_sessions_are_removed_in_batches_and_a_second_run_finds_nothing(world):
    codes = []
    for i in range(7):
        session = await world.graded_session(f"Пачка {i}", students=("Анна",))
        await world.age(session["code"], 250)
        codes.append(session["code"])
    result = await purge_closed_sessions(TestingSessionLocal, 180, batch_size=3)
    assert result.sessions >= 7
    for code in codes:
        assert await world.count("queue_sessions", "code = :c", c=code) == 0
    again = await purge_closed_sessions(TestingSessionLocal, 180, batch_size=3)
    assert again == PurgeResult(0, 0)


@pytest.mark.asyncio
async def test_concurrent_cleanups_do_not_clash(world):
    codes = []
    for i in range(6):
        session = await world.graded_session(f"Гонка {i}", students=("Анна",))
        await world.age(session["code"], 250)
        codes.append(session["code"])
    results = await asyncio.gather(
        *(purge_closed_sessions(TestingSessionLocal, 180, batch_size=2) for _ in range(4))
    )
    assert sum(r.sessions for r in results) >= 6
    for code in codes:
        assert await world.count("queue_sessions", "code = :c", c=code) == 0


@pytest.mark.asyncio
async def test_an_open_session_is_never_removed_even_with_an_old_closing_time(world):
    session = await world.graded_session("Закрывается")
    await world.age(session["code"], 250, status="open")
    result = await purge_closed_sessions(TestingSessionLocal, 180)
    assert await world.count("queue_sessions", "code = :c", c=session["code"]) == 1
    assert isinstance(result, PurgeResult)


@pytest.mark.asyncio
async def test_other_teachers_sessions_follow_the_same_rule(world, make_teacher):
    other = await make_teacher(f"ret_other_{datetime.now().strftime('%H%M%S%f')}")
    session = (await world.client.post(S, headers=other, json={"title": "Чужая"})).json()
    await world.client.post(f"{S}/{session['code']}/close", headers=other)
    await world.age(session["code"], 365)
    await purge_closed_sessions(TestingSessionLocal, 180)
    assert await world.count("queue_sessions", "code = :c", c=session["code"]) == 0


@pytest.mark.asyncio
async def test_a_zero_day_period_removes_every_closed_session_but_not_open_ones(world):
    closed = await world.graded_session("Закрытая")
    await world.client.post(f"{S}/{closed['code']}/close", headers=world.headers)
    await world.db.execute(
        text("UPDATE queue_sessions SET closed_at = now() - interval '1 second' WHERE code = :c"),
        {"c": closed["code"]},
    )
    await world.db.commit()
    opened = await world.graded_session("Открытая сегодня")
    await purge_closed_sessions(TestingSessionLocal, 0)
    assert await world.count("queue_sessions", "code = :c", c=closed["code"]) == 0
    assert await world.count("queue_sessions", "code = :c", c=opened["code"]) == 1


@pytest.mark.asyncio
async def test_the_command_line_counts_by_default_and_deletes_with_apply(
    world, monkeypatch, capsys
):
    session = await world.graded_session("CLI")
    await world.age(session["code"], 220)
    own_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    monkeypatch.setattr(retention, "AsyncSessionLocal", async_sessionmaker(own_engine))
    monkeypatch.setattr(retention, "engine", own_engine)

    def run(*args):
        return main(list(args))

    assert await asyncio.to_thread(run, "--days", "180") == 0
    assert "would remove" in capsys.readouterr().out
    assert await world.count("queue_sessions", "code = :c", c=session["code"]) == 1
    assert await asyncio.to_thread(run, "--days", "180", "--apply") == 0
    assert "removed" in capsys.readouterr().out
    assert await world.count("queue_sessions", "code = :c", c=session["code"]) == 0
    with pytest.raises(SystemExit):
        main(["--days", "-1"])


@pytest.mark.asyncio
async def test_the_default_period_is_half_a_year_and_the_loop_survives_errors(monkeypatch):
    assert settings.DATA_RETENTION_DAYS == 180
    assert settings.RETENTION_INTERVAL_SECONDS >= 60
    calls = []

    async def flaky(factory, days, **kwargs):
        calls.append(days)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return PurgeResult(2, 1)

    monkeypatch.setattr(retention, "purge_closed_sessions", flaky)
    task = asyncio.create_task(retention_loop(TestingSessionLocal, 7, 0.01))
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(calls) >= 2 and set(calls) == {7}
