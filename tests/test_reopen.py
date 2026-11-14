import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from core.config import settings
from services.maintenance import close_idle_sessions
from tests.conftest import TestingSessionLocal

API = "/api/v1"


@pytest.fixture
def lesson(client, make_teacher, make_session, join):
    async def _lesson(prefix: str, *, students: int = 2, graded: bool = True):
        headers = await make_teacher(f"{prefix}_{uuid.uuid4().hex[:6]}")
        code = await make_session(headers)
        if graded:
            made = await client.post(
                f"{API}/assignments",
                headers=headers,
                json={"title": f"Работа {prefix}", "questions_per_submission": 1},
            )
            assignment = made.json()
            await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
            )
            criterion = (
                await client.post(
                    f"{API}/assignments/{assignment['id']}/criteria",
                    headers=headers,
                    json={"name": "Ответ", "max_points": 5},
                )
            ).json()
            await client.put(
                f"{API}/sessions/{code}/assignment",
                headers=headers,
                json={"assignment_id": assignment["id"]},
            )
        else:
            criterion = None
        people = [await join(code, f"Студент {i}") for i in range(students)]
        return headers, code, people, criterion

    return _lesson


async def current(client, headers, code):
    response = await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
    return response


async def submissions(client, headers, code):
    return (await client.get(f"{API}/sessions/{code}/submissions", headers=headers)).json()


async def queue(client, headers, code):
    return (await client.get(f"{API}/sessions/{code}/queue", headers=headers)).json()


@pytest.mark.asyncio
async def test_reopening_restores_the_submission_cancelled_by_the_close(client, lesson):
    headers, code, _, _ = await lesson("ro_restore")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    assert (await current(client, headers, code)).status_code == 200

    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    closed = await current(client, headers, code)
    reopened = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    after = await current(client, headers, code)

    assert closed.status_code == 404
    assert reopened.status_code == 200 and reopened.json()["status"] == "open"
    assert after.status_code == 200 and after.json()["status"] == "in_progress"
    assert (await queue(client, headers, code))["status"] == "open"


@pytest.mark.asyncio
async def test_a_restored_submission_can_be_graded_and_finished(client, lesson):
    headers, code, _, criterion = await lesson("ro_grade")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    submission = (await current(client, headers, code)).json()
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    done = await client.post(
        f"{API}/submissions/{submission['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 4, "comment": ""}]},
    )

    assert done.status_code == 200, done.text
    assert done.json()["total_points"] == 4


@pytest.mark.asyncio
async def test_finished_and_skipped_work_is_left_alone(client, lesson):
    headers, code, _, criterion = await lesson("ro_alone", students=3)
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    first = (await current(client, headers, code)).json()
    await client.post(
        f"{API}/submissions/{first['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 5, "comment": ""}]},
    )
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    second = (await current(client, headers, code)).json()
    skipped = await client.post(
        f"{API}/sessions/{code}/entries/{second['entry_id']}/skip", headers=headers
    )
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    third = (await current(client, headers, code)).json()
    assert skipped.status_code == 200

    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    states = {s["id"]: s["status"] for s in await submissions(client, headers, code)}

    assert states[first["id"]] == "completed"
    assert states[second["id"]] == "cancelled"
    assert states[third["id"]] == "in_progress"


@pytest.mark.asyncio
async def test_students_are_accepted_again_after_reopening(client, lesson, join):
    headers, code, _, _ = await lesson("ro_join", graded=False)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    refused = await client.post(
        f"{API}/sessions/{code}/join", json={"name": "Поздний", "token": "z" * 20}
    )

    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    welcomed = await join(code, "Поздний", token="z" * 20)

    assert refused.status_code == 409 and refused.json()["code"] == "session_closed"
    assert welcomed["status"] == "waiting"


@pytest.mark.asyncio
async def test_reopening_an_open_session_changes_nothing(client, lesson):
    headers, code, _, _ = await lesson("ro_open", graded=False)

    first = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    events = (await client.get(f"{API}/sessions/{code}/events-log", headers=headers)).json()

    assert first.status_code == 200 and first.json()["status"] == "open"
    assert [e["kind"] for e in events] == []


@pytest.mark.asyncio
async def test_closing_and_reopening_leave_a_trace_in_the_session_journal(client, lesson):
    headers, code, _, _ = await lesson("ro_log")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    log = (await client.get(f"{API}/sessions/{code}/events-log", headers=headers)).json()
    events = [e for e in log if e["kind"] != "questions_drawn"]

    assert [(e["kind"], e["details"]) for e in events] == [
        ("close", {"cancelled_submissions": 1}),
        ("reopen", {"restored_submissions": 1}),
    ]


@pytest.mark.asyncio
async def test_only_the_owner_can_reopen(client, lesson, make_teacher):
    headers, code, _, _ = await lesson("ro_owner", graded=False)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    stranger = await make_teacher(f"ro_stranger_{uuid.uuid4().hex[:6]}")

    foreign = await client.post(f"{API}/sessions/{code}/reopen", headers=stranger)
    anonymous = await client.post(f"{API}/sessions/{code}/reopen")

    assert foreign.status_code == 404
    assert anonymous.status_code == 401
    assert (await queue(client, headers, code))["status"] == "closed"


async def age_the_close(code: str, hours: float):
    async with TestingSessionLocal() as db:
        await db.execute(
            text("UPDATE queue_sessions SET closed_at = :at WHERE code = :code"),
            {"at": datetime.now(UTC) - timedelta(hours=hours), "code": code},
        )
        await db.commit()


@pytest.mark.asyncio
async def test_a_session_closed_long_ago_cannot_be_reopened(client, lesson):
    headers, code, _, _ = await lesson("ro_old", graded=False)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await age_the_close(code, settings.SESSION_REOPEN_HOURS + 1)

    response = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    assert response.status_code == 409 and response.json()["code"] == "reopen_expired"
    assert (await queue(client, headers, code))["status"] == "closed"


@pytest.mark.asyncio
async def test_a_close_just_inside_the_window_can_still_be_undone(client, lesson):
    headers, code, _, _ = await lesson("ro_edge", graded=False)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await age_the_close(code, settings.SESSION_REOPEN_HOURS - 0.5)

    response = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    assert response.status_code == 200 and response.json()["status"] == "open"


@pytest.mark.asyncio
async def test_reopening_can_be_switched_off(client, lesson, monkeypatch):
    headers, code, _, _ = await lesson("ro_off", graded=False)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    monkeypatch.setattr(settings, "SESSION_REOPEN_HOURS", 0)

    response = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    assert response.status_code == 409 and response.json()["code"] == "reopen_expired"


@pytest.mark.asyncio
async def test_a_session_closed_by_the_idle_reaper_can_be_reopened_with_its_submission(
    client, lesson
):
    headers, code, _, _ = await lesson("ro_reaper")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)

    closed = await close_idle_sessions(
        TestingSessionLocal, hours=1, now=datetime.now(UTC) + timedelta(hours=2)
    )
    reopened = await client.post(f"{API}/sessions/{code}/reopen", headers=headers)
    after = await current(client, headers, code)

    assert code in closed
    assert reopened.status_code == 200
    assert after.status_code == 200 and after.json()["status"] == "in_progress"


@pytest.mark.asyncio
async def test_a_submission_reopened_by_requeue_survives_a_close_and_reopen(client, lesson):
    headers, code, _, _ = await lesson("ro_requeue", students=1)
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    called = (await current(client, headers, code)).json()
    await client.post(f"{API}/sessions/{code}/entries/{called['entry_id']}/skip", headers=headers)
    await client.post(
        f"{API}/sessions/{code}/entries/{called['entry_id']}/requeue", headers=headers
    )
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)
    await client.post(f"{API}/sessions/{code}/reopen", headers=headers)

    states = [s["status"] for s in await submissions(client, headers, code)]

    assert states == ["in_progress"]


@pytest.mark.asyncio
async def test_closing_and_reopening_at_once_end_in_a_consistent_state(client, lesson):
    headers, code, _, _ = await lesson("ro_race")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    await client.post(f"{API}/sessions/{code}/close", headers=headers)

    results = await asyncio.gather(
        client.post(f"{API}/sessions/{code}/reopen", headers=headers),
        client.post(f"{API}/sessions/{code}/close", headers=headers),
        client.post(f"{API}/sessions/{code}/reopen", headers=headers),
    )
    state = (await queue(client, headers, code))["status"]
    states = [s["status"] for s in await submissions(client, headers, code)]

    assert all(r.status_code == 200 for r in results)
    assert (state, states) in (("open", ["in_progress"]), ("closed", ["cancelled"]))
