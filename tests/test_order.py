import asyncio

import pytest

from repositories.queue import QueueRepository

API = "/api/v1/sessions"


async def entries(client, headers, code):
    return (await client.get(f"{API}/{code}/queue", headers=headers)).json()["entries"]


async def names(client, headers, code, status="waiting"):
    return [e["name"] for e in await entries(client, headers, code) if e["status"] == status]


async def eid(client, headers, code, name):
    return next(e["id"] for e in await entries(client, headers, code) if e["name"] == name)


async def act(client, headers, code, action, entry=None):
    suffix = f"entries/{entry}/{action}" if entry else action
    return await client.post(f"{API}/{code}/{suffix}", headers=headers)


async def me(client, code, student):
    response = await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": student["token"]})
    return response.json()


@pytest.fixture
def room(make_teacher, make_session, join):
    async def _room(username: str, people: tuple[str, ...]):
        headers = await make_teacher(username)
        code = await make_session(headers)
        students = {name: await join(code, name) for name in people}
        return headers, code, students

    return _room


@pytest.mark.asyncio
async def test_ticket_never_changes_after_promote_and_requeue(client, room):
    headers, code, students = await room("order_ticket", ("Anna", "Boris", "Clara", "Dan"))
    before = {n: s["ticket"] for n, s in students.items()}

    await act(client, headers, code, "promote", await eid(client, headers, code, "Dan"))
    await act(client, headers, code, "promote", await eid(client, headers, code, "Clara"))
    called = (await act(client, headers, code, "next")).json()
    await act(client, headers, code, "skip", called["id"])
    await act(client, headers, code, "requeue", called["id"])

    for name, student in students.items():
        assert (await me(client, code, student))["ticket"] == before[name]
    assert {e["name"]: e["ticket"] for e in await entries(client, headers, code)} == before


@pytest.mark.asyncio
async def test_promote_orders_by_sort_order_and_positions_follow(client, room):
    headers, code, students = await room("order_promote", ("A", "B", "C", "D"))

    await act(client, headers, code, "promote", await eid(client, headers, code, "C"))
    await act(client, headers, code, "promote", await eid(client, headers, code, "D"))

    assert await names(client, headers, code) == ["D", "C", "A", "B"]
    positions = {n: (await me(client, code, s))["position"] for n, s in students.items()}
    assert positions == {"D": 1, "C": 2, "A": 3, "B": 4}
    waits = {n: (await me(client, code, s))["eta_seconds"] for n, s in students.items()}
    assert waits == {"D": 0, "C": 300, "A": 600, "B": 900}


@pytest.mark.asyncio
async def test_promote_does_not_jump_over_the_called_student(client, room):
    headers, code, _ = await room("order_called", ("A", "B", "C", "D"))
    called = (await act(client, headers, code, "next")).json()
    assert called["name"] == "A"

    await act(client, headers, code, "promote", await eid(client, headers, code, "D"))

    assert [e["name"] for e in await entries(client, headers, code)] == ["A", "D", "B", "C"]
    await act(client, headers, code, "done", called["id"])
    assert (await act(client, headers, code, "next")).json()["name"] == "D"


@pytest.mark.asyncio
async def test_order_after_skip_requeue_leave_and_promote(client, room):
    headers, code, students = await room("order_mix", ("A", "B", "C", "D", "E"))
    a = (await act(client, headers, code, "next")).json()
    await act(client, headers, code, "skip", a["id"])
    await act(client, headers, code, "promote", await eid(client, headers, code, "D"))
    await client.post(f"{API}/{code}/leave", headers={"X-Entry-Token": students["C"]["token"]})
    assert await names(client, headers, code) == ["D", "B", "E"]

    await act(client, headers, code, "requeue", a["id"])
    assert await names(client, headers, code) == ["D", "B", "E", "A"]

    late = await students_join(client, code, "F")
    assert late["status"] == "waiting"
    assert await names(client, headers, code) == ["D", "B", "E", "A", "F"]
    assert (await me(client, code, students["A"]))["position"] == 4


async def students_join(client, code, name):
    response = await client.post(
        f"{API}/{code}/join", json={"name": name, "token": name.ljust(16, "x")}
    )
    return response.json()


@pytest.mark.asyncio
async def test_called_student_stays_first_even_with_promoted_waiting(client, room):
    headers, code, _ = await room("order_listing", ("A", "B", "C"))
    await act(client, headers, code, "next")
    await act(client, headers, code, "promote", await eid(client, headers, code, "C"))

    statuses = [(e["name"], e["status"]) for e in await entries(client, headers, code)]
    assert statuses == [("A", "called"), ("C", "waiting"), ("B", "waiting")]


@pytest.mark.asyncio
async def test_concurrent_promotes_and_next_keep_a_consistent_queue(client, room, monkeypatch):
    names_ = tuple(f"s{i:02d}" for i in range(10))
    headers, code, _ = await room("order_race", names_)
    ids = [await eid(client, headers, code, n) for n in names_[2:10]]

    original = QueueRepository.next_waiting

    async def slow_next_waiting(self, session_id):
        result = await original(self, session_id)
        await asyncio.sleep(0.05)
        return result

    monkeypatch.setattr(QueueRepository, "next_waiting", slow_next_waiting)
    calls = [act(client, headers, code, "promote", i) for i in ids] + [
        act(client, headers, code, "next") for _ in range(3)
    ]
    responses = await asyncio.gather(*calls)

    assert all(r.status_code in (200, 409) for r in responses), [r.status_code for r in responses]
    assert sum(1 for r in responses[len(ids) :] if r.status_code == 200) == 1
    rows = await entries(client, headers, code)
    assert sum(1 for e in rows if e["status"] == "called") == 1
    assert len({e["ticket"] for e in rows}) == len(rows) == 10
