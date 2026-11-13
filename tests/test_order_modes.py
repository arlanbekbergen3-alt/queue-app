import asyncio
import random
from collections import Counter

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.live import hub, student_stream
from services.ordering import banded, draw_order, is_banded, lottery_plan, unbanded
from tests.test_live import event

API = "/api/v1"
S = f"{API}/sessions"


@pytest_asyncio.fixture(autouse=True)
async def clean_hub():
    yield
    await hub.close()
    hub._states.clear()


def tok(student):
    return {"X-Entry-Token": student["token"]}


@pytest.fixture
def room(client, make_teacher):
    async def _room(username: str, **settings):
        headers = await make_teacher(username)
        response = await client.post(S, headers=headers, json={"title": "Сдача", **settings})
        assert response.status_code == 201, response.text
        return headers, response.json()["code"]

    return _room


@pytest.fixture
def enter(client):
    async def _enter(code: str, name: str, **extra) -> dict:
        token = name.encode().hex().ljust(16, "x")
        response = await client.post(
            f"{S}/{code}/join", json={"name": name, "token": token, **extra}
        )
        assert response.status_code == 200, response.text
        return {**response.json(), "token": token, "name": name}

    return _enter


async def queue(client, headers, code):
    return (await client.get(f"{S}/{code}/queue", headers=headers)).json()


async def order(client, headers, code):
    return [e["name"] for e in (await queue(client, headers, code))["entries"]]


async def me(client, code, student):
    return (await client.get(f"{S}/{code}/me", headers=tok(student))).json()


async def shuffle(client, headers, code):
    return await client.post(f"{S}/{code}/lottery", headers=headers)


async def set_ready(client, code, student, value):
    return await client.put(f"{S}/{code}/priority", headers=tok(student), json={"ready_now": value})


async def tickets(client, headers, code):
    return {e["name"]: e["ticket"] for e in (await queue(client, headers, code))["entries"]}


def test_banding_is_reversible_and_detectable():
    for value in (1, 5, 999, -3):
        assert not is_banded(value)
        assert unbanded(banded(value)) == value
        assert is_banded(banded(value))
        assert banded(banded(value)) == banded(value)
        assert unbanded(value) == value


def test_draw_is_a_permutation_and_repeatable_for_a_fixed_seed():
    ids = [f"e{i}" for i in range(20)]
    first = draw_order(ids, random.Random(7))
    assert sorted(first) == sorted(ids)
    assert first == draw_order(ids, random.Random(7))
    assert first != draw_order(ids, random.Random(8))
    assert draw_order([]) == []
    assert draw_order(["a"]) == ["a"]


def test_every_permutation_of_three_is_drawn_about_equally_often():
    rng = random.Random(2024)
    counts = Counter(tuple(draw_order(["a", "b", "c"], rng)) for _ in range(12000))
    assert len(counts) == 6
    assert all(1800 < n < 2200 for n in counts.values()), counts


def test_first_position_is_uniform_for_ten_people():
    rng = random.Random(99)
    ids = [str(i) for i in range(10)]
    counts = Counter(draw_order(ids, rng)[0] for _ in range(20000))
    assert all(1700 < counts[i] < 2300 for i in ids), counts


def test_plan_numbers_groups_in_order_and_leaves_the_rest_last():
    drawn, numbers = lottery_plan([["a", "b"], ["c", "d", "e"]], ["x", "y"], random.Random(1))
    assert sorted(numbers.values()) == list(range(1, 8))
    assert {numbers["a"], numbers["b"]} == {1, 2}
    assert {numbers["c"], numbers["d"], numbers["e"]} == {3, 4, 5}
    assert (numbers["x"], numbers["y"]) == (6, 7)
    assert [sorted(g) for g in drawn] == [["a", "b"], ["c", "d", "e"]]


@pytest.mark.asyncio
async def test_defaults_keep_the_old_behaviour(client, room, enter):
    headers, code = await room("om_default")
    session = (await client.get(f"{S}/{code}")).json()
    assert (session["order_mode"], session["ready_priority"]) == ("join", False)
    assert (session["ready_bonus_points"], session["ready_skip_questions"]) == (0, 0)
    first = await enter(code, "Анна", ready_now=True)
    second = await enter(code, "Борис")
    assert (first["ticket"], first["in_pool"], first["ready_now"]) == (1, False, False)
    assert second["position"] == 2
    assert (await shuffle(client, headers, code)).status_code == 409
    assert (await shuffle(client, headers, code)).json()["code"] == "lottery_off"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field,value",
    [
        ("order_mode", "random"),
        ("ready_bonus_points", 6),
        ("ready_bonus_points", -1),
        ("ready_skip_questions", 2),
    ],
)
async def test_session_settings_are_validated(client, make_teacher, field, value):
    headers = await make_teacher(f"om_val_{field}_{value}".replace("-", "m"))
    response = await client.post(S, headers=headers, json={"title": "X", field: value})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_pool_hides_numbers_and_counts_signed_up_students(client, room, enter):
    headers, code = await room("om_pool", order_mode="lottery")
    anna = await enter(code, "Анна")
    boris = await enter(code, "Борис")
    view = await me(client, code, anna)
    assert view["ticket"] is None
    assert (view["in_pool"], view["pool_count"], view["position"]) == (True, 2, None)
    assert (view["people_ahead"], view["eta_seconds"]) == (None, None)
    assert (await me(client, code, boris))["pool_count"] == 2
    assert (await client.get(f"{S}/{code}")).json()["lottery_done"] is False


@pytest.mark.asyncio
async def test_calling_is_blocked_until_the_lottery_is_done(client, room, enter):
    headers, code = await room("om_block", order_mode="lottery")
    await enter(code, "Анна")
    blocked = await client.post(f"{S}/{code}/next", headers=headers)
    assert blocked.status_code == 409 and blocked.json()["code"] == "lottery_pending"
    entry = (await queue(client, headers, code))["entries"][0]
    moved = await client.post(f"{S}/{code}/entries/{entry['id']}/promote", headers=headers)
    assert moved.status_code == 409 and moved.json()["code"] == "lottery_pending"
    assert "перемешайте" in blocked.json()["detail"]
    assert (await shuffle(client, headers, code)).status_code == 200
    assert (await client.post(f"{S}/{code}/next", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_lottery_numbers_everybody_once_and_closes_registration(client, room, enter):
    headers, code = await room("om_draw", order_mode="lottery")
    students = [await enter(code, f"Студент {i}") for i in range(8)]
    response = await shuffle(client, headers, code)
    assert response.status_code == 200
    body = response.json()
    assert body["join_open"] is False and body["lottery_at"] is not None
    numbers = await tickets(client, headers, code)
    assert sorted(numbers.values()) == list(range(1, 9))
    for student in students:
        view = await me(client, code, student)
        assert view["ticket"] == numbers[student["name"]]
        assert view["in_pool"] is False and view["position"] == view["ticket"]
    listed = await order(client, headers, code)
    assert listed == sorted(listed, key=lambda name: numbers[name])
    late = await client.post(f"{S}/{code}/join", json={"name": "Поздно", "token": "p" * 20})
    assert late.status_code == 409 and late.json()["code"] == "registration_closed"


@pytest.mark.asyncio
async def test_second_lottery_changes_nothing_and_logs_nothing(client, room, enter):
    headers, code = await room("om_idem", order_mode="lottery")
    for i in range(6):
        await enter(code, f"Студент {i}")
    await shuffle(client, headers, code)
    before = await tickets(client, headers, code)
    again = await shuffle(client, headers, code)
    assert again.status_code == 200
    assert await tickets(client, headers, code) == before
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert [(e["kind"], e["details"]["participants"]) for e in log] == [("lottery", 6)]


@pytest.mark.asyncio
async def test_lottery_is_written_to_the_journal_with_the_final_order(client, room, enter):
    headers, code = await room("om_journal", order_mode="lottery")
    for i in range(5):
        await enter(code, f"С{i}")
    await shuffle(client, headers, code)
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert len(log) == 1
    details = log[0]["details"]
    assert (details["group"], details["participants"], details["first_ticket"]) == ("all", 5, 1)
    by_ticket = sorted((await queue(client, headers, code))["entries"], key=lambda e: e["ticket"])
    assert details["order"] == [e["id"] for e in by_ticket]
    assert "name" not in str(details)


@pytest.mark.asyncio
async def test_journal_is_append_only(client, room, enter, db_session):
    headers, code = await room("om_append", order_mode="lottery")
    await enter(code, "Анна")
    await shuffle(client, headers, code)
    with pytest.raises(DBAPIError):
        await db_session.execute(text("UPDATE session_events SET kind = 'x'"))
    await db_session.rollback()
    with pytest.raises(DBAPIError):
        await db_session.execute(text("DELETE FROM session_events"))
    await db_session.rollback()


@pytest.mark.asyncio
async def test_only_the_owner_can_run_the_lottery_or_read_the_journal(
    client, room, enter, make_teacher
):
    headers, code = await room("om_owner", order_mode="lottery")
    await enter(code, "Анна")
    other = await make_teacher("om_owner_other")
    assert (await shuffle(client, other, code)).status_code == 404
    assert (await client.get(f"{S}/{code}/events-log", headers=other)).status_code == 404
    assert (await client.get(f"{S}/{code}/events-log")).status_code == 401
    assert (await client.post(f"{S}/{code}/lottery")).status_code == 401
    assert (await client.put(f"{S}/{code}/settings", headers=other, json={})).status_code == 404
    assert (await me(client, code, {"token": "z" * 20}))["code"] == "entry_not_found"
    assert (await client.get(f"{S}/{code}")).json()["lottery_done"] is False


@pytest.mark.asyncio
async def test_lottery_on_a_closed_session_is_refused(client, room, enter):
    headers, code = await room("om_closed", order_mode="lottery")
    await enter(code, "Анна")
    await client.post(f"{S}/{code}/close", headers=headers)
    response = await shuffle(client, headers, code)
    assert response.status_code == 409 and response.json()["code"] == "session_closed"


@pytest.mark.asyncio
async def test_latecomers_join_the_end_after_the_lottery(client, room, enter):
    headers, code = await room("om_late", order_mode="lottery")
    early = [await enter(code, f"Рано {i}") for i in range(4)]
    await shuffle(client, headers, code)
    before = await tickets(client, headers, code)
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": True})
    first = await enter(code, "Поздний 1")
    second = await enter(code, "Поздний 2")
    assert (first["ticket"], second["ticket"]) == (5, 6)
    assert (first["in_pool"], first["position"]) == (False, 5)
    after = await tickets(client, headers, code)
    assert {k: v for k, v in after.items() if k in before} == before
    assert (await order(client, headers, code))[-2:] == ["Поздний 1", "Поздний 2"]
    assert [(await me(client, code, s))["ticket"] for s in early] == [
        before[s["name"]] for s in early
    ]


@pytest.mark.asyncio
async def test_lottery_with_skipped_and_left_students_keeps_numbers_unique(client, room, enter):
    headers, code = await room("om_mixed", order_mode="lottery")
    students = [await enter(code, f"С{i}") for i in range(6)]
    await client.post(f"{S}/{code}/leave", headers=tok(students[0]))
    skipped = next(e for e in (await queue(client, headers, code))["entries"] if e["name"] == "С1")
    await client.post(f"{S}/{code}/entries/{skipped['id']}/skip", headers=headers)
    await shuffle(client, headers, code)
    waiting = [
        e for e in (await queue(client, headers, code))["entries"] if e["status"] == "waiting"
    ]
    assert sorted(e["ticket"] for e in waiting) == [1, 2, 3, 4]
    row = await me(client, code, students[1])
    assert row["status"] == "skipped" and row["ticket"] in (5, 6)
    requeued = await client.post(f"{S}/{code}/entries/{skipped['id']}/requeue", headers=headers)
    assert requeued.status_code == 200
    assert (await order(client, headers, code))[-1] == "С1"


@pytest.mark.asyncio
async def test_not_ready_and_promote_work_after_the_lottery(client, room, enter):
    headers, code = await room("om_nr", order_mode="lottery")
    students = [await enter(code, f"С{i}") for i in range(4)]
    assert (await client.post(f"{S}/{code}/not-ready", headers=tok(students[0]))).status_code == 200
    await shuffle(client, headers, code)
    listed = await order(client, headers, code)
    assert listed[-1] == "С0"
    last = (await queue(client, headers, code))["entries"][-2]
    moved = await client.post(f"{S}/{code}/entries/{last['id']}/promote", headers=headers)
    assert moved.status_code == 200
    assert (await order(client, headers, code))[0] == last["name"]
    called = await client.post(f"{S}/{code}/next", headers=headers)
    assert called.json()["name"] == last["name"]
    back = await client.post(f"{S}/{code}/ready", headers=tok(students[0]))
    assert back.status_code == 200 and back.json()["not_ready"] is False


@pytest.mark.asyncio
async def test_students_receive_their_numbers_over_the_live_stream(client, room, enter):
    headers, code = await room("om_sse", order_mode="lottery")
    anna = await enter(code, "Анна")
    await enter(code, "Борис")
    entry = (await queue(client, headers, code))["entries"][0]
    stream = student_stream(code, entry["id"])
    name, first = await event(stream)
    assert name == "entry" and first["ticket"] is None and first["in_pool"] is True
    await shuffle(client, headers, code)
    name, second = await event(stream)
    assert second["ticket"] in (1, 2) and second["in_pool"] is False
    await stream.aclose()
    assert anna["token"]


@pytest.mark.asyncio
async def test_ready_choice_is_ignored_when_priority_is_off(client, room, enter):
    headers, code = await room("om_off")
    first = await enter(code, "Анна")
    second = await enter(code, "Борис", ready_now=True)
    assert second["ready_now"] is False
    assert await order(client, headers, code) == ["Анна", "Борис"]
    response = await set_ready(client, code, first, True)
    assert response.status_code == 409 and response.json()["code"] == "ready_priority_off"


@pytest.mark.asyncio
async def test_ready_students_go_first_then_everybody_else_by_arrival(client, room, enter):
    headers, code = await room("om_ready", ready_priority=True)
    first = await enter(code, "Первый")
    second = await enter(code, "Второй", ready_now=True)
    await enter(code, "Третий")
    fourth = await enter(code, "Четвёртый", ready_now=True)
    assert await order(client, headers, code) == ["Второй", "Четвёртый", "Первый", "Третий"]
    assert (second["ready_now"], second["position"]) == (True, 1)
    assert (await me(client, code, fourth))["position"] == 2
    assert (await me(client, code, first))["position"] == 3


@pytest.mark.asyncio
async def test_ready_choice_can_change_until_registration_closes(client, room, enter):
    headers, code = await room("om_toggle", ready_priority=True)
    first = await enter(code, "Первый")
    second = await enter(code, "Второй")
    assert (await set_ready(client, code, second, True)).json()["position"] == 1
    assert await order(client, headers, code) == ["Второй", "Первый"]
    assert (await set_ready(client, code, second, False)).json()["position"] == 2
    assert await order(client, headers, code) == ["Первый", "Второй"]
    again = await set_ready(client, code, first, True)
    assert again.json()["ready_now"] is True
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": False})
    locked = await set_ready(client, code, second, True)
    assert locked.status_code == 409 and locked.json()["code"] == "ready_locked"
    assert "Запись закрыта" in locked.json()["detail"]


@pytest.mark.asyncio
async def test_ready_choice_survives_promote_requeue_and_not_ready(client, room, enter):
    headers, code = await room("om_combo", ready_priority=True)
    a = await enter(code, "А", ready_now=True)
    await enter(code, "Б")
    c = await enter(code, "В", ready_now=True)
    await client.post(f"{S}/{code}/not-ready", headers=tok(a))
    assert await order(client, headers, code) == ["В", "Б", "А"]
    await client.post(f"{S}/{code}/ready", headers=tok(a))
    assert await order(client, headers, code) == ["А", "В", "Б"]
    entry = {e["name"]: e for e in (await queue(client, headers, code))["entries"]}
    await client.post(f"{S}/{code}/entries/{entry['Б']['id']}/promote", headers=headers)
    assert await order(client, headers, code) == ["Б", "А", "В"]
    await client.post(f"{S}/{code}/entries/{entry['А']['id']}/skip", headers=headers)
    await client.post(f"{S}/{code}/entries/{entry['А']['id']}/requeue", headers=headers)
    assert await order(client, headers, code) == ["Б", "В", "А"]
    assert c["ready_now"] is True


@pytest.mark.asyncio
async def test_priority_can_be_switched_on_and_off_before_registration_closes(client, room, enter):
    headers, code = await room("om_switch")
    await enter(code, "Первый")
    second = await enter(code, "Второй")
    changed = await client.put(
        f"{S}/{code}/settings", headers=headers, json={"ready_priority": True}
    )
    assert changed.status_code == 200 and changed.json()["ready_priority"] is True
    await set_ready(client, code, second, True)
    assert await order(client, headers, code) == ["Второй", "Первый"]
    await client.put(f"{S}/{code}/settings", headers=headers, json={"ready_priority": False})
    assert await order(client, headers, code) == ["Первый", "Второй"]
    await client.put(f"{S}/{code}/settings", headers=headers, json={"ready_priority": True})
    assert await order(client, headers, code) == ["Второй", "Первый"]


@pytest.mark.asyncio
async def test_settings_are_locked_once_registration_closes(client, room, enter):
    headers, code = await room("om_locked")
    await enter(code, "Анна")
    ok = await client.put(
        f"{S}/{code}/settings",
        headers=headers,
        json={"order_mode": "lottery", "ready_bonus_points": 3, "ready_skip_questions": 1},
    )
    assert ok.status_code == 200
    assert (ok.json()["order_mode"], ok.json()["ready_bonus_points"]) == ("lottery", 3)
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": False})
    locked = await client.put(f"{S}/{code}/settings", headers=headers, json={"order_mode": "join"})
    assert locked.status_code == 409 and locked.json()["code"] == "settings_locked"
    bad = await client.put(f"{S}/{code}/settings", headers=headers, json={"ready_bonus_points": 9})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_lottery_draws_the_ready_group_separately_and_logs_each_draw(client, room, enter):
    headers, code = await room("om_both", order_mode="lottery", ready_priority=True)
    ready = [await enter(code, f"Готов {i}", ready_now=True) for i in range(3)]
    waiting = [await enter(code, f"Ждёт {i}") for i in range(4)]
    view = await me(client, code, ready[0])
    assert view["in_pool"] is True and view["ready_now"] is True and view["pool_count"] == 7
    await shuffle(client, headers, code)
    numbers = await tickets(client, headers, code)
    assert {numbers[s["name"]] for s in ready} == {1, 2, 3}
    assert {numbers[s["name"]] for s in waiting} == {4, 5, 6, 7}
    listed = await order(client, headers, code)
    assert {n.split()[0] for n in listed[:3]} == {"Готов"}
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert [(e["details"]["group"], e["details"]["participants"]) for e in log] == [
        ("ready", 3),
        ("rest", 4),
    ]
    assert [e["details"]["first_ticket"] for e in log] == [1, 4]


@pytest.mark.asyncio
async def test_ready_choice_before_the_draw_can_still_change(client, room, enter):
    headers, code = await room("om_pre", order_mode="lottery", ready_priority=True)
    anna = await enter(code, "Анна")
    boris = await enter(code, "Борис")
    await set_ready(client, code, anna, True)
    await set_ready(client, code, boris, True)
    await set_ready(client, code, boris, False)
    await shuffle(client, headers, code)
    numbers = await tickets(client, headers, code)
    assert (numbers["Анна"], numbers["Борис"]) == (1, 2)
    late = await set_ready(client, code, boris, True)
    assert late.status_code == 409


@pytest.mark.asyncio
async def test_latecomer_cannot_jump_ahead_by_claiming_readiness(client, room, enter):
    headers, code = await room("om_jump", order_mode="lottery", ready_priority=True)
    await enter(code, "Анна", ready_now=True)
    await enter(code, "Борис")
    await shuffle(client, headers, code)
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": True})
    late = await enter(code, "Поздний", ready_now=True)
    assert late["ready_now"] is False and late["position"] == 3
    assert (await order(client, headers, code))[-1] == "Поздний"


@pytest.mark.asyncio
async def test_concurrent_joins_get_distinct_numbers_in_either_mode(client, room):
    headers, code = await room("om_race_join", ready_priority=True)

    async def one(i):
        return await client.post(
            f"{S}/{code}/join",
            json={"name": f"С{i}", "token": f"{i:02d}".ljust(20, "x"), "ready_now": i % 2 == 0},
        )

    responses = await asyncio.gather(*(one(i) for i in range(16)))
    assert all(r.status_code == 200 for r in responses)
    listed = (await queue(client, headers, code))["entries"]
    assert len({e["ticket"] for e in listed}) == 16
    ready = [e["ready_now"] for e in listed]
    assert ready == sorted(ready, reverse=True)


@pytest.mark.asyncio
async def test_concurrent_lotteries_run_exactly_once(client, room, enter):
    headers, code = await room("om_race_draw", order_mode="lottery")
    for i in range(12):
        await enter(code, f"С{i}")
    responses = await asyncio.gather(*(shuffle(client, headers, code) for _ in range(6)))
    assert [r.status_code for r in responses] == [200] * 6
    assert len({r.json()["lottery_at"] for r in responses}) == 1
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    assert len(log) == 1
    assert sorted((await tickets(client, headers, code)).values()) == list(range(1, 13))


@pytest.mark.asyncio
async def test_joining_while_the_lottery_runs_never_breaks_numbering(client, room, enter):
    headers, code = await room("om_race_mix", order_mode="lottery")
    for i in range(8):
        await enter(code, f"С{i}")

    async def late(i):
        return await client.post(
            f"{S}/{code}/join", json={"name": f"П{i}", "token": f"late{i:02d}".ljust(20, "x")}
        )

    results = await asyncio.gather(shuffle(client, headers, code), *(late(i) for i in range(6)))
    assert results[0].status_code == 200
    listed = (await queue(client, headers, code))["entries"]
    numbers = [e["ticket"] for e in listed]
    assert len(numbers) == len(set(numbers))
    assert sorted(numbers) == list(range(1, len(numbers) + 1))
    joined_after = [r for r in results[1:] if r.status_code == 200]
    assert len(listed) == 8 + len(joined_after)


@pytest.mark.asyncio
async def test_changing_readiness_while_the_lottery_runs_is_consistent(client, room, enter):
    headers, code = await room("om_race_ready", order_mode="lottery", ready_priority=True)
    students = [await enter(code, f"С{i}") for i in range(6)]
    results = await asyncio.gather(
        shuffle(client, headers, code),
        *(set_ready(client, code, s, True) for s in students),
    )
    assert results[0].status_code == 200
    assert {r.status_code for r in results[1:]} <= {200, 409}
    log = (await client.get(f"{S}/{code}/events-log", headers=headers)).json()
    groups = {e["details"]["group"]: e["details"]["participants"] for e in log}
    ready_count = groups.get("ready", 0)
    numbers = await tickets(client, headers, code)
    assert sorted(numbers.values()) == list(range(1, 7))
    flagged = [e for e in (await queue(client, headers, code))["entries"] if e["ready_now"]]
    assert len(flagged) == ready_count
    assert all(e["ticket"] <= ready_count for e in flagged)


async def completed_session(client, room, enter, name, **settings):
    headers, code = await room(name, **settings)
    assignment = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {name}", "questions_per_submission": 3},
        )
    ).json()
    for i in range(6):
        await client.post(
            f"{API}/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос {i}", "difficulty": 1, "topic": "t"},
        )
    criterion = (
        await client.post(
            f"{API}/assignments/{assignment['id']}/criteria",
            headers=headers,
            json={"name": "Код", "max_points": 10},
        )
    ).json()
    await client.put(
        f"{S}/{code}/assignment", headers=headers, json={"assignment_id": assignment["id"]}
    )
    return headers, code, criterion, assignment


async def serve(client, headers, code, criterion, points=8):
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": points}], "comment": ""},
    )
    assert done.status_code == 200, done.text
    return current, done.json()


@pytest.mark.asyncio
async def test_bonus_is_a_separate_line_for_early_students_only(client, room, enter):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_bonus", ready_priority=True, ready_bonus_points=2
    )
    ready = await enter(code, "Готовый", ready_now=True)
    other = await enter(code, "Обычный")
    first, done = await serve(client, headers, code, criterion)
    assert done["bonus_points"] == 2
    assert (done["total_points"], done["max_points"]) == (8, 10)
    mine = (await client.get(f"{S}/{code}/me/submission", headers=tok(ready))).json()
    assert (mine["bonus_points"], mine["total_points"]) == (2, 8)
    _, second = await serve(client, headers, code, criterion)
    assert second["bonus_points"] == 0
    assert (await client.get(f"{S}/{code}/me/submission", headers=tok(other))).json()[
        "bonus_points"
    ] == 0
    audit = (await client.get(f"{API}/submissions/{first['id']}/audit", headers=headers)).json()
    assert [a["new_value"] for a in audit if a["action"] == "bonus"] == ["2"]


@pytest.mark.asyncio
async def test_bonus_is_visible_in_the_student_history_and_the_card(client, room, enter):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_bonus_hist", ready_priority=True, ready_bonus_points=1
    )
    ready = await enter(code, "Готовый", ready_now=True)
    await serve(client, headers, code, criterion)
    history = (await client.get(f"{S}/{code}/me/history", headers=tok(ready))).json()
    assert history["attempts"][0]["bonus_points"] == 1
    students = (await client.get(f"{API}/students", headers=headers)).json()
    card = (await client.get(f"{API}/students/{students[0]['id']}", headers=headers)).json()
    assert card["assignments"][0]["attempts"][0]["bonus_points"] == 1


@pytest.mark.asyncio
async def test_no_bonus_without_priority_or_when_zero(client, room, enter):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_nobonus", ready_priority=True, ready_bonus_points=0
    )
    await enter(code, "Готовый", ready_now=True)
    _, done = await serve(client, headers, code, criterion)
    assert done["bonus_points"] == 0
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_nobonus2", ready_priority=False, ready_bonus_points=3
    )
    await enter(code, "Готовый", ready_now=True)
    _, done = await serve(client, headers, code, criterion)
    assert done["bonus_points"] == 0


@pytest.mark.asyncio
async def test_teacher_removes_a_bonus_with_a_reason_and_it_is_logged(
    client, room, enter, make_teacher
):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_unbonus", ready_priority=True, ready_bonus_points=2
    )
    ready = await enter(code, "Готовый", ready_now=True)
    current, _ = await serve(client, headers, code, criterion)
    url = f"{API}/submissions/{current['id']}/bonus/remove"
    assert (await client.post(url, headers=headers, json={"reason": "   "})).status_code == 422
    assert (await client.post(url, headers=headers, json={})).status_code == 422
    other = await make_teacher("om_unbonus_other")
    assert (await client.post(url, headers=other, json={"reason": "нет"})).status_code == 404
    assert (await client.post(url, json={"reason": "нет"})).status_code == 401
    removed = await client.post(url, headers=headers, json={"reason": "Списал ответ"})
    assert removed.status_code == 200 and removed.json()["bonus_points"] == 0
    again = await client.post(url, headers=headers, json={"reason": "Ещё раз"})
    assert again.status_code == 409 and again.json()["code"] == "no_bonus"
    mine = (await client.get(f"{S}/{code}/me/submission", headers=tok(ready))).json()
    assert mine["bonus_points"] == 0
    audit = (await client.get(f"{API}/submissions/{current['id']}/audit", headers=headers)).json()
    entry = next(a for a in audit if a["action"] == "bonus_remove")
    assert (entry["old_value"], entry["new_value"], entry["reason"]) == ("2", "0", "Списал ответ")


@pytest.mark.asyncio
async def test_ready_students_get_one_question_fewer_but_never_zero(client, room, enter):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_skipq", ready_priority=True, ready_skip_questions=1
    )
    await enter(code, "Готовый", ready_now=True)
    await enter(code, "Обычный")
    first, _ = await serve(client, headers, code, criterion)
    second, _ = await serve(client, headers, code, criterion)
    assert len(first["questions"]) == 2
    assert len(second["questions"]) == 3

    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_skipq1", ready_priority=True, ready_skip_questions=1
    )
    await client.patch(
        f"{API}/assignments/{assignment['id']}",
        headers=headers,
        json={"questions_per_submission": 1},
    )
    await enter(code, "Готовый", ready_now=True)
    only, _ = await serve(client, headers, code, criterion)
    assert len(only["questions"]) == 1


@pytest.mark.asyncio
async def test_perks_are_shown_to_students_and_teachers(client, room, enter):
    headers, code = await room(
        "om_perks", ready_priority=True, ready_bonus_points=2, ready_skip_questions=1
    )
    anna = await enter(code, "Анна", ready_now=True)
    view = await me(client, code, anna)
    assert (view["ready_priority"], view["ready_bonus_points"], view["ready_skip_questions"]) == (
        True,
        2,
        1,
    )
    public = (await client.get(f"{S}/{code}")).json()
    assert (public["ready_bonus_points"], public["ready_skip_questions"]) == (2, 1)
    teacher = await queue(client, headers, code)
    assert (teacher["ready_priority"], teacher["ready_bonus_points"]) == (True, 2)
    assert teacher["entries"][0]["ready_now"] is True


@pytest.mark.asyncio
async def test_a_false_readiness_claim_costs_nothing(client, room, enter):
    headers, code, criterion, assignment = await completed_session(
        client, room, enter, "om_nopenalty", ready_priority=True, ready_bonus_points=2
    )
    claimer = await enter(code, "Заявил", ready_now=True)
    await client.post(f"{S}/{code}/not-ready", headers=tok(claimer))
    _, done = await serve(client, headers, code, criterion, points=0)
    assert done["total_points"] == 0 and done["max_points"] == 10
    assert done["bonus_points"] == 2
