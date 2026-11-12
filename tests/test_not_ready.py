import asyncio

import pytest
import pytest_asyncio

from core.config import settings
from services.live import hub, student_stream, teacher_stream
from services.queue import build_student_view
from tests.test_live import event

API = "/api/v1/sessions"


def tok(student):
    return {"X-Entry-Token": student["token"]}


async def queue(client, headers, code):
    return (await client.get(f"{API}/{code}/queue", headers=headers)).json()


async def entry_of(client, headers, code, name):
    return next(e for e in (await queue(client, headers, code))["entries"] if e["name"] == name)


async def me(client, code, student):
    return (await client.get(f"{API}/{code}/me", headers=tok(student))).json()


async def defer(client, code, student):
    return await client.post(f"{API}/{code}/not-ready", headers=tok(student))


async def ready(client, code, student):
    return await client.post(f"{API}/{code}/ready", headers=tok(student))


async def next_up(client, headers, code):
    return await client.post(f"{API}/{code}/next", headers=headers)


async def registration(client, headers, code, is_open):
    return await client.put(f"{API}/{code}/registration", headers=headers, json={"open": is_open})


@pytest_asyncio.fixture(autouse=True)
async def clean_hub():
    yield
    await hub.close()
    hub._states.clear()


@pytest.fixture
def room(make_teacher, make_session, join):
    async def _room(username: str, people: tuple[str, ...]):
        headers = await make_teacher(username)
        code = await make_session(headers)
        students = {name: await join(code, name) for name in people}
        return headers, code, students

    return _room


def test_student_view_reports_deferral_only_while_waiting():
    kwargs = dict(
        ticket=1,
        name="A",
        waiting_before=0,
        called_ticket=None,
        called_name=None,
        avg_service_seconds=300,
        session_status="open",
        not_ready=True,
        not_ready_count=1,
    )
    waiting = build_student_view(status="waiting", **kwargs)
    called = build_student_view(status="called", **kwargs)
    assert (waiting.not_ready, waiting.not_ready_count) == (True, 1)
    assert waiting.not_ready_left == settings.NOT_READY_MAX_PER_SESSION - 1
    assert called.not_ready is False


def test_left_count_never_goes_below_zero():
    view = build_student_view(
        ticket=1,
        name="A",
        status="waiting",
        waiting_before=0,
        called_ticket=None,
        called_name=None,
        avg_service_seconds=300,
        session_status="open",
        not_ready=False,
        not_ready_count=99,
    )
    assert view.not_ready_left == 0


@pytest.mark.asyncio
async def test_not_ready_student_keeps_ticket_and_moves_behind_ready_ones(client, room):
    headers, code, s = await room("nr_order", ("Anna", "Boris", "Clara"))

    response = await defer(client, code, s["Anna"])

    assert response.status_code == 200
    body = response.json()
    assert (body["ticket"], body["position"], body["not_ready"]) == (1, 3, True)
    assert (await me(client, code, s["Boris"]))["position"] == 1
    assert (await me(client, code, s["Clara"]))["position"] == 2
    names = [e["name"] for e in (await queue(client, headers, code))["entries"]]
    assert names == ["Boris", "Clara", "Anna"]


@pytest.mark.asyncio
async def test_call_next_takes_ready_students_first_then_the_deferred_one(client, room):
    headers, code, s = await room("nr_call", ("Anna", "Boris"))
    await defer(client, code, s["Anna"])

    first = (await next_up(client, headers, code)).json()
    assert first["name"] == "Boris"
    await client.post(f"{API}/{code}/entries/{first['id']}/done", headers=headers)
    second = (await next_up(client, headers, code)).json()
    assert second["name"] == "Anna"


@pytest.mark.asyncio
async def test_ready_returns_the_student_to_the_place_by_ticket(client, room):
    headers, code, s = await room("nr_back", ("Anna", "Boris", "Clara"))
    await defer(client, code, s["Anna"])

    response = await ready(client, code, s["Anna"])

    assert response.status_code == 200
    body = response.json()
    assert (body["position"], body["not_ready"], body["ticket"]) == (1, False, 1)
    assert [e["name"] for e in (await queue(client, headers, code))["entries"]] == [
        "Anna",
        "Boris",
        "Clara",
    ]


@pytest.mark.asyncio
async def test_two_deferred_students_keep_ticket_order_among_themselves(client, room):
    headers, code, s = await room("nr_two", ("Anna", "Boris", "Clara"))
    await defer(client, code, s["Boris"])
    await defer(client, code, s["Anna"])

    names = [e["name"] for e in (await queue(client, headers, code))["entries"]]
    assert names == ["Clara", "Anna", "Boris"]


@pytest.mark.asyncio
async def test_limit_of_deferrals_per_session_is_enforced(client, room):
    headers, code, s = await room("nr_limit", ("Anna", "Boris"))
    anna = s["Anna"]
    for _ in range(settings.NOT_READY_MAX_PER_SESSION):
        assert (await defer(client, code, anna)).status_code == 200
        assert (await ready(client, code, anna)).status_code == 200

    response = await defer(client, code, anna)

    assert response.status_code == 409
    assert response.json()["code"] == "not_ready_limit"
    assert "не готов" in response.json()["detail"]
    view = await me(client, code, anna)
    assert (view["not_ready"], view["not_ready_left"]) == (False, 0)
    assert (await defer(client, code, s["Boris"])).status_code == 200


@pytest.mark.asyncio
async def test_limit_is_configurable(client, room, monkeypatch):
    monkeypatch.setattr(settings, "NOT_READY_MAX_PER_SESSION", 0)
    headers, code, s = await room("nr_limit0", ("Anna",))

    response = await defer(client, code, s["Anna"])

    assert response.status_code == 409
    assert response.json()["code"] == "not_ready_limit"

    monkeypatch.setattr(settings, "NOT_READY_MAX_PER_SESSION", 3)
    for _ in range(3):
        assert (await defer(client, code, s["Anna"])).status_code == 200
        await ready(client, code, s["Anna"])
    assert (await defer(client, code, s["Anna"])).status_code == 409


@pytest.mark.asyncio
async def test_repeated_not_ready_does_not_spend_another_mark(client, room):
    headers, code, s = await room("nr_idem", ("Anna", "Boris"))

    for _ in range(4):
        assert (await defer(client, code, s["Anna"])).status_code == 200

    view = await me(client, code, s["Anna"])
    assert (view["not_ready_count"], view["not_ready_left"]) == (1, 1)
    assert (await ready(client, code, s["Anna"])).status_code == 200
    assert (await ready(client, code, s["Anna"])).status_code == 200


@pytest.mark.asyncio
async def test_only_a_waiting_student_can_change_readiness(client, room):
    headers, code, s = await room("nr_state", ("Anna", "Boris"))
    await next_up(client, headers, code)

    called = await defer(client, code, s["Anna"])
    assert (called.status_code, called.json()["code"]) == (409, "not_waiting")
    assert (await ready(client, code, s["Anna"])).status_code == 409

    entry = await entry_of(client, headers, code, "Anna")
    await client.post(f"{API}/{code}/entries/{entry['id']}/skip", headers=headers)
    assert (await defer(client, code, s["Anna"])).status_code == 409

    await client.post(f"{API}/{code}/leave", headers=tok(s["Boris"]))
    assert (await defer(client, code, s["Boris"])).status_code == 409


@pytest.mark.asyncio
async def test_not_ready_is_rejected_in_a_closed_session(client, room):
    headers, code, s = await room("nr_closed", ("Anna",))
    await client.post(f"{API}/{code}/close", headers=headers)

    response = await defer(client, code, s["Anna"])

    assert (response.status_code, response.json()["code"]) == (409, "session_closed")


@pytest.mark.asyncio
async def test_students_cannot_change_each_others_readiness(client, room, make_teacher, join):
    headers, code, s = await room("nr_owner", ("Anna", "Boris"))
    other_headers = await make_teacher("nr_owner_other")
    other_code = (
        await client.post(f"{API}", headers=other_headers, json={"title": "Other"})
    ).json()["code"]
    outsider = await join(other_code, "Stranger", "z" * 20)

    wrong_token = await client.post(
        f"{API}/{code}/not-ready", headers={"X-Entry-Token": outsider["token"]}
    )
    no_token = await client.post(f"{API}/{code}/not-ready")
    unknown_session = await client.post(f"{API}/ZZZZZZ/not-ready", headers=tok(s["Anna"]))

    assert wrong_token.status_code == 404
    assert no_token.status_code == 401
    assert unknown_session.status_code == 404
    assert (await me(client, code, s["Anna"]))["not_ready"] is False
    assert (await me(client, code, s["Boris"]))["not_ready"] is False
    assert (await entry_of(client, other_headers, other_code, "Stranger"))["not_ready"] is False


@pytest.mark.asyncio
async def test_teacher_sees_the_mark_and_counter_but_other_teachers_cannot_read_it(
    client, room, make_teacher
):
    headers, code, s = await room("nr_teacher", ("Anna", "Boris"))
    await defer(client, code, s["Anna"])
    await ready(client, code, s["Anna"])
    await defer(client, code, s["Anna"])

    entry = await entry_of(client, headers, code, "Anna")

    assert (entry["not_ready"], entry["not_ready_count"]) == (True, 2)
    assert (await queue(client, headers, code))[
        "not_ready_max"
    ] == settings.NOT_READY_MAX_PER_SESSION
    stranger = await make_teacher("nr_teacher_other")
    assert (await client.get(f"{API}/{code}/queue", headers=stranger)).status_code == 404


@pytest.mark.asyncio
async def test_teacher_promote_clears_the_mark_and_puts_the_student_first(client, room):
    headers, code, s = await room("nr_promote", ("Anna", "Boris", "Clara"))
    await defer(client, code, s["Anna"])
    entry = await entry_of(client, headers, code, "Anna")

    response = await client.post(f"{API}/{code}/entries/{entry['id']}/promote", headers=headers)

    assert response.status_code == 200
    assert (await entry_of(client, headers, code, "Anna"))["not_ready"] is False
    assert [e["name"] for e in (await queue(client, headers, code))["entries"]] == [
        "Anna",
        "Boris",
        "Clara",
    ]
    assert (await me(client, code, s["Anna"]))["position"] == 1


@pytest.mark.asyncio
async def test_teacher_skip_and_requeue_still_work_and_requeue_clears_the_mark(client, room):
    headers, code, s = await room("nr_skip", ("Anna", "Boris"))
    await defer(client, code, s["Anna"])
    entry = await entry_of(client, headers, code, "Anna")

    skipped = await client.post(f"{API}/{code}/entries/{entry['id']}/skip", headers=headers)
    back = await client.post(f"{API}/{code}/entries/{entry['id']}/requeue", headers=headers)

    assert skipped.json()["status"] == "skipped"
    assert back.json()["status"] == "waiting"
    after = await entry_of(client, headers, code, "Anna")
    assert after["not_ready"] is False
    assert after["not_ready_count"] == 1
    assert (await me(client, code, s["Anna"]))["position"] == 2


@pytest.mark.asyncio
async def test_registration_can_be_closed_and_reopened(client, room, join):
    headers, code, s = await room("reg_basic", ("Anna",))

    closed = await registration(client, headers, code, False)
    assert (closed.status_code, closed.json()["join_open"]) == (200, False)
    info = (await client.get(f"{API}/{code}")).json()
    assert (info["status"], info["join_open"]) == ("open", False)
    assert (await queue(client, headers, code))["join_open"] is False

    rejected = await client.post(f"{API}/{code}/join", json={"name": "Late", "token": "l" * 20})
    assert rejected.status_code == 409
    assert rejected.json()["detail"] == "Запись закрыта"
    assert rejected.json()["code"] == "registration_closed"

    await registration(client, headers, code, True)
    assert (await join(code, "Late", "l" * 20))["ticket"] == 2


@pytest.mark.asyncio
async def test_closed_registration_does_not_touch_students_already_in_the_queue(client, room, join):
    headers, code, s = await room("reg_existing", ("Anna", "Boris"))
    await registration(client, headers, code, False)

    again = await join(code, "Anna", s["Anna"]["token"])
    assert again["ticket"] == 1
    assert (await me(client, code, s["Boris"]))["position"] == 2
    called = (await next_up(client, headers, code)).json()
    assert called["name"] == "Anna"
    assert (await defer(client, code, s["Boris"])).status_code == 200
    assert (await me(client, code, s["Anna"]))["status"] == "called"


@pytest.mark.asyncio
async def test_only_the_owner_can_change_registration(client, room, make_teacher):
    headers, code, _ = await room("reg_owner", ("Anna",))
    stranger = await make_teacher("reg_owner_other")

    assert (await registration(client, stranger, code, False)).status_code == 404
    assert (await client.put(f"{API}/{code}/registration", json={"open": False})).status_code == 401
    assert (await client.get(f"{API}/{code}")).json()["join_open"] is True


@pytest.mark.asyncio
async def test_registration_requires_a_boolean_and_a_session_that_is_open(client, room):
    headers, code, _ = await room("reg_state", ("Anna",))

    bad = await client.put(f"{API}/{code}/registration", headers=headers, json={})
    assert bad.status_code == 422

    await client.post(f"{API}/{code}/close", headers=headers)
    reopened = await registration(client, headers, code, True)
    assert (reopened.status_code, reopened.json()["code"]) == (409, "session_closed")
    assert (await registration(client, headers, code, False)).status_code == 200


@pytest.mark.asyncio
async def test_closed_session_message_still_wins_over_closed_registration(client, room):
    headers, code, _ = await room("reg_both", ("Anna",))
    await registration(client, headers, code, False)
    await client.post(f"{API}/{code}/close", headers=headers)

    response = await client.post(f"{API}/{code}/join", json={"name": "Late", "token": "q" * 20})

    assert (response.status_code, response.json()["code"]) == (409, "session_closed")


@pytest.mark.asyncio
async def test_student_stream_follows_not_ready_and_ready(client, room):
    headers, code, s = await room("nr_sse_student", ("Anna", "Boris"))
    entry = await entry_of(client, headers, code, "Anna")
    stream = student_stream(code, entry["id"])
    assert (await event(stream))[1]["position"] == 1

    await defer(client, code, s["Anna"])
    _, deferred = await event(stream)
    assert (deferred["not_ready"], deferred["position"]) == (True, 2)

    await ready(client, code, s["Anna"])
    _, back = await event(stream)
    assert (back["not_ready"], back["position"]) == (False, 1)
    await stream.aclose()


@pytest.mark.asyncio
async def test_teacher_stream_follows_marks_and_registration(client, room):
    headers, code, s = await room("nr_sse_teacher", ("Anna", "Boris"))
    stream = teacher_stream(code)
    _, first = await event(stream)
    assert first["join_open"] is True

    await defer(client, code, s["Anna"])
    _, marked = await event(stream)
    assert marked["entries"][-1]["name"] == "Anna"
    assert marked["entries"][-1]["not_ready"] is True

    await registration(client, headers, code, False)
    _, closed = await event(stream)
    assert closed["join_open"] is False
    await stream.aclose()


@pytest.mark.asyncio
async def test_concurrent_not_ready_from_one_student_spends_one_mark(client, room):
    headers, code, s = await room("race_same", ("Anna", "Boris"))

    results = await asyncio.gather(*(defer(client, code, s["Anna"]) for _ in range(10)))

    assert {r.status_code for r in results} == {200}
    view = await me(client, code, s["Anna"])
    assert (view["not_ready"], view["not_ready_count"]) == (True, 1)


@pytest.mark.asyncio
async def test_concurrent_toggling_never_exceeds_the_limit(client, room):
    headers, code, s = await room("race_toggle", ("Anna", "Boris"))
    jobs = []
    for _ in range(8):
        jobs.append(defer(client, code, s["Anna"]))
        jobs.append(ready(client, code, s["Anna"]))

    results = await asyncio.gather(*jobs)

    assert {r.status_code for r in results} <= {200, 409}
    view = await me(client, code, s["Anna"])
    assert view["not_ready_count"] <= settings.NOT_READY_MAX_PER_SESSION
    assert view["not_ready_left"] == settings.NOT_READY_MAX_PER_SESSION - view["not_ready_count"]
    assert all(r.json()["code"] == "not_ready_limit" for r in results if r.status_code == 409)


@pytest.mark.asyncio
async def test_not_ready_racing_call_next_never_calls_a_deferred_student_over_a_ready_one(
    client, room
):
    for round_no in range(6):
        headers, code, s = await room(f"race_next_{round_no}", ("Anna", "Boris"))

        marked, called = await asyncio.gather(
            defer(client, code, s["Anna"]), next_up(client, headers, code)
        )

        assert called.status_code == 200
        if marked.status_code == 200:
            assert called.json()["name"] == "Boris"
        else:
            assert (marked.status_code, marked.json()["code"]) == (409, "not_waiting")
            assert called.json()["name"] == "Anna"


@pytest.mark.asyncio
async def test_ready_racing_call_next_leaves_a_consistent_queue(client, room):
    for round_no in range(6):
        headers, code, s = await room(f"race_ready_{round_no}", ("Anna", "Boris"))
        await defer(client, code, s["Anna"])

        back, called = await asyncio.gather(
            ready(client, code, s["Anna"]), next_up(client, headers, code)
        )

        assert (back.status_code, called.status_code) == (200, 200)
        entries = (await queue(client, headers, code))["entries"]
        assert sum(1 for e in entries if e["status"] == "called") == 1
        assert called.json()["name"] in ("Anna", "Boris")
        others = [e for e in entries if e["status"] == "waiting"]
        assert [e["not_ready"] for e in others] == [False]


@pytest.mark.asyncio
async def test_closing_registration_races_with_joins_without_losing_consistency(
    client, make_teacher, make_session
):
    headers = await make_teacher("race_reg")
    code = await make_session(headers)

    async def join_once(i: int):
        return await client.post(
            f"{API}/{code}/join", json={"name": f"S{i}", "token": f"t{i}".ljust(20, "x")}
        )

    jobs = [join_once(i) for i in range(12)]
    jobs.insert(6, registration(client, headers, code, False))
    results = await asyncio.gather(*jobs)

    joined = [r for r in results if r.status_code == 200 and "ticket" in r.json()]
    refused = [r for r in results if r.status_code == 409]
    assert all(r.json()["code"] == "registration_closed" for r in refused)
    assert len(joined) + len(refused) == 12
    entries = (await queue(client, headers, code))["entries"]
    assert len(entries) == len(joined)
    late = await join_once(99)
    assert (late.status_code, late.json()["code"]) == (409, "registration_closed")
    assert len((await queue(client, headers, code))["entries"]) == len(joined)


@pytest.mark.asyncio
async def test_toggling_registration_races_with_not_ready_and_next(client, room):
    headers, code, s = await room("race_mix", ("Anna", "Boris", "Clara"))

    results = await asyncio.gather(
        registration(client, headers, code, False),
        defer(client, code, s["Anna"]),
        next_up(client, headers, code),
        defer(client, code, s["Boris"]),
        registration(client, headers, code, True),
    )

    assert all(r.status_code in (200, 409) for r in results)
    entries = (await queue(client, headers, code))["entries"]
    assert sum(1 for e in entries if e["status"] == "called") == 1
    waiting = [e for e in entries if e["status"] == "waiting"]
    flags = [e["not_ready"] for e in waiting]
    assert flags == sorted(flags)
