import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

import services.budget as budget_module
from services.budget import forecast, reduced_end

API = "/api/v1"
S = f"{API}/sessions"
T0 = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)


def at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def test_budget_is_ninety_percent_of_remaining_time_split_between_students():
    calc = forecast(
        T0, at(60), waiting=9, serving_elapsed=None, avg_service_seconds=300, reserve_percent=10
    )
    assert calc.remaining_seconds == 3600
    assert calc.reserve_seconds == 360
    assert calc.remaining_students == 9
    assert calc.budget_seconds == (3600 - 360) // 9 == 360


def test_the_student_being_served_counts_as_remaining():
    calc = forecast(
        T0, at(30), waiting=4, serving_elapsed=60, avg_service_seconds=300, reserve_percent=10
    )
    assert calc.remaining_students == 5
    assert calc.work_seconds == 4 * 300 + 240
    assert calc.forecast_end == T0 + timedelta(seconds=1440)


def test_serving_longer_than_average_adds_no_negative_time():
    calc = forecast(
        T0, at(30), waiting=1, serving_elapsed=900, avg_service_seconds=300, reserve_percent=10
    )
    assert calc.work_seconds == 300


def test_status_is_ok_tight_or_over():
    ok = forecast(T0, at(60), 6, None, 300, 10)
    assert (ok.work_seconds, ok.status) == (1800, "ok")
    tight = forecast(T0, at(30), 6, None, 300, 10)
    assert (tight.work_seconds, tight.status) == (1800, "tight")
    over = forecast(T0, at(20), 6, None, 300, 10)
    assert over.status == "over"
    edge = forecast(T0, at(33.333334), 6, None, 300, 10)
    assert edge.status in ("ok", "tight")


def test_empty_queue_is_idle_and_has_no_budget():
    calc = forecast(T0, at(30), 0, None, 300, 10)
    assert (calc.status, calc.budget_seconds, calc.remaining_students) == ("idle", None, 0)
    assert calc.forecast_end == T0


def test_zero_remaining_time_gives_a_zero_budget_and_over():
    calc = forecast(T0, at(-5), 3, None, 300, 10)
    assert (calc.remaining_seconds, calc.budget_seconds, calc.status) == (0, 0, "over")
    calc = forecast(T0, T0, 1, None, 300, 10)
    assert (calc.budget_seconds, calc.status) == (0, "over")


def test_zero_reserve_uses_all_the_time():
    calc = forecast(T0, at(10), 2, None, 300, 0)
    assert (calc.reserve_seconds, calc.budget_seconds, calc.status) == (0, 300, "ok")


def test_reducing_questions_scales_the_remaining_work_proportionally():
    assert reduced_end(T0, 1800, 3, 2) == T0 + timedelta(seconds=1200)
    assert reduced_end(T0, 1800, 2, 1) == T0 + timedelta(seconds=900)
    assert reduced_end(T0, 0, 3, 2) == T0


@pytest.fixture
def room(client, make_teacher):
    async def _room(username: str, **extra):
        headers = await make_teacher(username)
        response = await client.post(S, headers=headers, json={"title": "Сдача", **extra})
        assert response.status_code == 201, response.text
        return headers, response.json()

    return _room


@pytest.fixture
def enter(client):
    async def _enter(code: str, name: str) -> dict:
        token = name.encode().hex().ljust(16, "x")
        response = await client.post(f"{S}/{code}/join", json={"name": name, "token": token})
        assert response.status_code == 200, response.text
        return {**response.json(), "token": token}

    return _enter


@pytest.fixture
def freeze(monkeypatch):
    def _freeze(moment: datetime):
        monkeypatch.setattr(budget_module, "utcnow", lambda: moment)

    return _freeze


async def set_average(db_session, code, seconds):
    await db_session.execute(
        text("UPDATE queue_sessions SET avg_service_seconds = :s WHERE code = :c"),
        {"s": seconds, "c": code},
    )
    await db_session.commit()


async def get_budget(client, headers, code):
    return (await client.get(f"{S}/{code}/budget", headers=headers)).json()


async def with_assignment(client, headers, code, questions=3, title="Работа"):
    assignment = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": title, "questions_per_submission": questions},
        )
    ).json()
    for i in range(8):
        await client.post(
            f"{API}/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос {i}", "difficulty": 1, "topic": "t"},
        )
    criterion = (
        await client.post(
            f"{API}/assignments/{assignment['id']}/criteria",
            headers=headers,
            json={"name": "Код", "max_points": 5},
        )
    ).json()
    await client.put(
        f"{S}/{code}/assignment", headers=headers, json={"assignment_id": assignment["id"]}
    )
    return assignment, criterion


@pytest.mark.asyncio
async def test_the_feature_is_hidden_without_a_time_limit(client, room, enter):
    headers, session = await room("bud_none")
    await enter(session["code"], "Анна")
    assert session["deadline_at"] is None
    assert await get_budget(client, headers, session["code"]) == {
        "enabled": False,
        "status": None,
        "deadline_at": None,
        "remaining_seconds": None,
        "remaining_students": None,
        "reserve_seconds": None,
        "budget_seconds": None,
        "avg_service_seconds": None,
        "forecast_end_at": None,
        "questions": None,
        "suggestion": None,
    }


@pytest.mark.asyncio
async def test_duration_in_minutes_sets_the_deadline(client, room):
    before = datetime.now(UTC)
    _, session = await room("bud_dur", duration_minutes=45)
    deadline = datetime.fromisoformat(session["deadline_at"])
    assert timedelta(minutes=44) < deadline - before < timedelta(minutes=46)


@pytest.mark.asyncio
async def test_an_absolute_deadline_is_accepted_with_a_time_zone(client, room):
    wanted = datetime.now(UTC) + timedelta(hours=2)
    _, session = await room("bud_abs", deadline_at=wanted.isoformat())
    assert abs((datetime.fromisoformat(session["deadline_at"]) - wanted).total_seconds()) < 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra,status,code",
    [
        ({"duration_minutes": 0}, 422, "validation_error"),
        ({"duration_minutes": 721}, 422, "validation_error"),
        (
            {"duration_minutes": 30, "deadline_at": "2099-01-01T10:00:00+00:00"},
            422,
            "validation_error",
        ),
        ({"deadline_at": "2026-01-01T10:00:00"}, 422, "validation_error"),
        ({"deadline_at": "2020-01-01T10:00:00+00:00"}, 422, "invalid_deadline"),
        ({"deadline_at": "2099-01-01T10:00:00+00:00"}, 422, "invalid_deadline"),
    ],
)
async def test_bad_time_limits_are_refused_with_a_clear_reason(
    client, make_teacher, extra, status, code
):
    headers = await make_teacher(f"bud_bad_{abs(hash(str(extra)))}")
    response = await client.post(S, headers=headers, json={"title": "X", **extra})
    assert response.status_code == status and response.json()["code"] == code
    if code == "invalid_deadline":
        assert "Время окончания" in response.json()["detail"]


@pytest.mark.asyncio
async def test_panel_numbers_for_a_known_queue(client, room, enter, db_session, freeze):
    headers, session = await room("bud_panel", duration_minutes=60)
    code = session["code"]
    for i in range(6):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    freeze(datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=60))
    data = await get_budget(client, headers, code)
    assert data["enabled"] is True
    assert (data["remaining_students"], data["remaining_seconds"]) == (6, 3600)
    assert (data["reserve_seconds"], data["budget_seconds"]) == (360, 540)
    assert (data["avg_service_seconds"], data["status"]) == (300, "ok")
    start = datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=60)
    assert datetime.fromisoformat(data["forecast_end_at"]) == start + timedelta(seconds=1800)
    assert data["suggestion"] is None and data["questions"] is None


@pytest.mark.asyncio
async def test_an_empty_queue_is_idle(client, room, freeze):
    headers, session = await room("bud_idle", duration_minutes=30)
    data = await get_budget(client, headers, session["code"])
    assert (data["status"], data["budget_seconds"], data["remaining_students"]) == ("idle", None, 0)
    assert data["suggestion"] is None


@pytest.mark.asyncio
async def test_a_deadline_in_the_past_gives_a_zero_budget(client, room, enter, freeze):
    headers, session = await room("bud_zero", duration_minutes=10)
    await enter(session["code"], "Анна")
    freeze(datetime.fromisoformat(session["deadline_at"]) + timedelta(minutes=5))
    data = await get_budget(client, headers, session["code"])
    assert (data["remaining_seconds"], data["budget_seconds"], data["status"]) == (0, 0, "over")


@pytest.mark.asyncio
async def test_the_forecast_is_recalculated_after_skips_and_returns(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_skip", duration_minutes=60)
    code = session["code"]
    for i in range(5):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    start = datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=60)
    freeze(start)
    assert (await get_budget(client, headers, code))["remaining_students"] == 5
    entries = (await client.get(f"{S}/{code}/queue", headers=headers)).json()["entries"]
    await client.post(f"{S}/{code}/entries/{entries[0]['id']}/skip", headers=headers)
    after_skip = await get_budget(client, headers, code)
    assert after_skip["remaining_students"] == 4
    assert datetime.fromisoformat(after_skip["forecast_end_at"]) == start + timedelta(seconds=1200)
    await client.post(f"{S}/{code}/entries/{entries[0]['id']}/requeue", headers=headers)
    back = await get_budget(client, headers, code)
    assert back["remaining_students"] == 5
    assert datetime.fromisoformat(back["forecast_end_at"]) == start + timedelta(seconds=1500)


@pytest.mark.asyncio
async def test_the_student_being_served_counts_and_shortens_the_forecast(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_called", duration_minutes=60)
    code = session["code"]
    for i in range(3):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    await client.post(f"{S}/{code}/next", headers=headers)
    called_at = (await client.get(f"{S}/{code}/queue", headers=headers)).json()["entries"][0][
        "called_at"
    ]
    freeze(datetime.fromisoformat(called_at) + timedelta(seconds=100))
    data = await get_budget(client, headers, code)
    assert data["remaining_students"] == 3
    end = datetime.fromisoformat(data["forecast_end_at"])
    assert end == datetime.fromisoformat(called_at) + timedelta(seconds=100 + 2 * 300 + 200)


@pytest.mark.asyncio
async def test_over_time_offers_to_reduce_questions_with_the_new_forecast(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_offer", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    for i in range(8):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    start = datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=30)
    freeze(start)
    data = await get_budget(client, headers, code)
    assert data["status"] == "over" and data["questions"] == 3
    offer = data["suggestion"]
    assert (offer["questions_from"], offer["questions_to"]) == (3, 2)
    assert datetime.fromisoformat(offer["forecast_end_at"]) == start + timedelta(seconds=1600)


@pytest.mark.asyncio
async def test_no_offer_when_on_time_or_without_an_assignment_or_at_one_question(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_nooffer", duration_minutes=30)
    code = session["code"]
    for i in range(8):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    freeze(datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=30))
    assert (await get_budget(client, headers, code))["status"] == "over"
    assert (await get_budget(client, headers, code))["suggestion"] is None
    await with_assignment(client, headers, code, questions=1)
    assert (await get_budget(client, headers, code))["suggestion"] is None
    await set_average(db_session, code, 30)
    assert (await get_budget(client, headers, code))["status"] == "ok"


@pytest.mark.asyncio
async def test_accepting_reduces_questions_for_the_next_students_and_logs_it(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_accept", duration_minutes=30)
    code = session["code"]
    _, criterion = await with_assignment(client, headers, code, questions=3)
    for i in range(8):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    freeze(datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=30))
    await client.post(f"{S}/{code}/next", headers=headers)
    first = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(first["questions"]) == 3
    await client.post(
        f"{API}/submissions/{first['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 4}], "comment": ""},
    )
    response = await client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": True, "questions": 2}
    )
    assert response.status_code == 200 and response.json()["questions"] == 2
    await client.post(f"{S}/{code}/next", headers=headers)
    second = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(second["questions"]) == 2 and second["questions_short"] is False
    assert len(first["questions"]) == 3
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    event = next(e for e in log if e["kind"] == "questions_reduced")
    assert (event["details"]["from"], event["details"]["to"]) == (3, 2)
    assert event["details"]["remaining_students"] == 7


@pytest.mark.asyncio
async def test_declining_changes_nothing_but_is_logged_and_hides_that_offer(
    client, room, enter, db_session, freeze
):
    headers, session = await room("bud_decline", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    for i in range(8):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    freeze(datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=30))
    response = await client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": False, "questions": 2}
    )
    assert response.status_code == 200
    assert response.json()["questions"] == 3 and response.json()["suggestion"] is None
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert [e["kind"] for e in log] == ["questions_declined"]
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(current["questions"]) == 3


@pytest.mark.asyncio
async def test_each_step_down_is_offered_in_turn(client, room, enter, db_session, freeze):
    headers, session = await room("bud_steps", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    for i in range(12):
        await enter(code, f"С{i}")
    await set_average(db_session, code, 300)
    freeze(datetime.fromisoformat(session["deadline_at"]) - timedelta(minutes=30))
    await client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": True, "questions": 2}
    )
    offer = (await get_budget(client, headers, code))["suggestion"]
    assert (offer["questions_from"], offer["questions_to"]) == (2, 1)
    last = await client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": True, "questions": 1}
    )
    assert last.json()["questions"] == 1 and last.json()["suggestion"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("questions", [3, 4, 10])
async def test_the_number_of_questions_can_only_go_down(
    client, room, enter, db_session, freeze, questions
):
    headers, session = await room(f"bud_up{questions}", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    response = await client.post(
        f"{S}/{code}/budget/decision",
        headers=headers,
        json={"accept": True, "questions": questions},
    )
    assert response.status_code == 409 and response.json()["code"] == "questions_not_reducible"
    assert "только уменьшить" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_decision_without_an_assignment_is_refused(client, room):
    headers, session = await room("bud_noasg", duration_minutes=30)
    response = await client.post(
        f"{S}/{session['code']}/budget/decision",
        headers=headers,
        json={"accept": True, "questions": 1},
    )
    assert response.status_code == 409 and "не задано" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_decision_on_a_closed_session_is_refused(client, room):
    headers, session = await room("bud_closed", duration_minutes=30)
    await with_assignment(client, headers, session["code"], questions=3)
    await client.post(f"{S}/{session['code']}/close", headers=headers)
    response = await client.post(
        f"{S}/{session['code']}/budget/decision",
        headers=headers,
        json={"accept": True, "questions": 2},
    )
    assert response.status_code == 409 and response.json()["code"] == "session_closed"


@pytest.mark.asyncio
async def test_only_the_owner_sees_or_changes_the_budget(client, room, make_teacher):
    headers, session = await room("bud_owner", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    other = await make_teacher("bud_owner_other")
    assert (await client.get(f"{S}/{code}/budget", headers=other)).status_code == 404
    body = {"accept": True, "questions": 2}
    assert (
        await client.post(f"{S}/{code}/budget/decision", headers=other, json=body)
    ).status_code == 404
    assert (await client.get(f"{S}/{code}/budget")).status_code == 401
    assert (await client.post(f"{S}/{code}/budget/decision", json=body)).status_code == 401
    assert (await client.get(f"{S}/ZZZZZZ/budget", headers=headers)).status_code == 404
    unchanged = await get_budget(client, headers, code)
    assert unchanged["questions"] == 3


@pytest.mark.asyncio
async def test_concurrent_decisions_apply_once_per_value(client, room):
    headers, session = await room("bud_race", duration_minutes=30)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    body = {"accept": True, "questions": 2}
    results = await asyncio.gather(
        *(client.post(f"{S}/{code}/budget/decision", headers=headers, json=body) for _ in range(6))
    )
    statuses = sorted(r.status_code for r in results)
    assert statuses == [200] + [409] * 5
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert [e["kind"] for e in log] == ["questions_reduced"]


@pytest.mark.asyncio
async def test_a_decision_racing_with_calls_gives_each_student_a_consistent_count(
    client, room, enter, db_session
):
    headers, session = await room("bud_race2", duration_minutes=30)
    code = session["code"]
    _, criterion = await with_assignment(client, headers, code, questions=3)
    for i in range(4):
        await enter(code, f"С{i}")
    await client.post(f"{S}/{code}/next", headers=headers)
    decide = client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": True, "questions": 2}
    )
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    finish = client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 3}], "comment": ""},
    )
    results = await asyncio.gather(decide, finish)
    assert [r.status_code for r in results] == [200, 200]
    await client.post(f"{S}/{code}/next", headers=headers)
    nxt = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(nxt["questions"]) == 2
    assert len(current["questions"]) == 3


@pytest.mark.asyncio
async def test_ready_skip_and_the_reduction_combine_without_going_below_one(
    client, room, enter, db_session
):
    headers, session = await room(
        "bud_combo", duration_minutes=30, ready_priority=True, ready_skip_questions=1
    )
    code = session["code"]
    await with_assignment(client, headers, code, questions=2)
    await client.post(
        f"{S}/{code}/budget/decision", headers=headers, json={"accept": True, "questions": 1}
    )
    token = "готов".encode().hex().ljust(16, "x")
    await client.post(f"{S}/{code}/join", json={"name": "Готов", "token": token, "ready_now": True})
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(current["questions"]) == 1 and current["questions_short"] is False


@pytest.mark.asyncio
async def test_a_ready_student_with_fewer_questions_is_not_marked_short(client, room):
    headers, session = await room("bud_short", ready_priority=True, ready_skip_questions=1)
    code = session["code"]
    await with_assignment(client, headers, code, questions=3)
    token = "готов".encode().hex().ljust(16, "x")
    await client.post(f"{S}/{code}/join", json={"name": "Готов", "token": token, "ready_now": True})
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(current["questions"]) == 2 and current["questions_short"] is False


@pytest.mark.asyncio
async def test_a_small_bank_is_still_reported_as_short(client, room):
    headers, session = await room("bud_bank")
    code = session["code"]
    assignment = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": "Малый банк", "questions_per_submission": 3},
        )
    ).json()
    await client.post(
        f"{API}/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Один", "difficulty": 1, "topic": "t"},
    )
    await client.put(
        f"{S}/{code}/assignment", headers=headers, json={"assignment_id": assignment["id"]}
    )
    token = "студент".encode().hex().ljust(16, "x")
    await client.post(f"{S}/{code}/join", json={"name": "Студент", "token": token})
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(current["questions"]) == 1 and current["questions_short"] is True
