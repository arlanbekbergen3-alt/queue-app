import asyncio

import pytest

from core.config import settings

API = "/api/v1/sessions"


@pytest.mark.asyncio
async def test_public_info_and_unknown_code(client, make_teacher, make_session):
    code = await make_session(await make_teacher("join_pub"), "Algorithms")
    info = (await client.get(f"{API}/{code}")).json()
    assert info == {
        "code": code,
        "title": "Algorithms",
        "group": None,
        "status": "open",
        "waiting_count": 0,
        "now_serving": None,
        "practice": False,
        "join_open": True,
        "order_mode": "join",
        "ready_priority": False,
        "ready_bonus_points": 0,
        "ready_skip_questions": 0,
        "lottery_done": False,
        "session_mode": "protocol",
        "presence_check": "off",
    }
    assert (await client.get(f"{API}/ZZZZZZ")).status_code == 404


@pytest.mark.asyncio
async def test_students_get_sequential_tickets_and_positions(make_teacher, make_session, join):
    code = await make_session(await make_teacher("join_seq"))
    first = await join(code, "Anna")
    second = await join(code, "Boris")
    assert (first["ticket"], first["position"], first["eta_seconds"]) == (1, 1, 0)
    assert (second["ticket"], second["position"], second["eta_seconds"]) == (2, 2, 300)


@pytest.mark.asyncio
async def test_join_is_idempotent_for_the_same_token(client, make_teacher, make_session, join):
    code = await make_session(await make_teacher("join_idem"))
    first = await join(code, "Anna", "a" * 20)
    again = await join(code, "Anna renamed", "a" * 20)
    assert again["ticket"] == first["ticket"] == 1
    assert again["name"] == "Anna"
    assert (await client.get(f"{API}/{code}")).json()["waiting_count"] == 1


@pytest.mark.asyncio
async def test_code_is_case_insensitive(client, make_teacher, make_session):
    code = await make_session(await make_teacher("join_case"))
    response = await client.post(
        f"{API}/{code.lower()}/join", json={"name": "Anna", "token": "t" * 20}
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_join_validation(client, make_teacher, make_session):
    code = await make_session(await make_teacher("join_valid"))
    url = f"{API}/{code}/join"
    assert (await client.post(url, json={"name": "   ", "token": "t" * 20})).status_code == 422
    assert (await client.post(url, json={"name": "x" * 61, "token": "t" * 20})).status_code == 422
    assert (await client.post(url, json={"name": "Anna", "token": "short"})).status_code == 422
    unknown = await client.post(f"{API}/ZZZZZZ/join", json={"name": "A", "token": "t" * 20})
    assert unknown.status_code == 404


@pytest.mark.asyncio
async def test_closed_session_rejects_new_students_but_keeps_existing(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("join_closed")
    code = await make_session(headers)
    existing = await join(code, "Anna")
    assert (await client.post(f"{API}/{code}/close", headers=headers)).status_code == 200

    late = await client.post(f"{API}/{code}/join", json={"name": "Late", "token": "l" * 20})
    assert late.status_code == 409
    again = await join(code, "Anna", existing["token"])
    assert again["ticket"] == existing["ticket"]
    assert again["session_status"] == "closed"


@pytest.mark.asyncio
async def test_queue_size_limit(client, make_teacher, make_session, monkeypatch):
    monkeypatch.setattr(settings, "MAX_QUEUE_SIZE", 2)
    code = await make_session(await make_teacher("join_limit"))
    for i in range(2):
        r = await client.post(f"{API}/{code}/join", json={"name": f"s{i}", "token": f"{i}" * 20})
        assert r.status_code == 200
    full = await client.post(f"{API}/{code}/join", json={"name": "extra", "token": "z" * 20})
    assert full.status_code == 409


@pytest.mark.asyncio
async def test_many_simultaneous_joins_get_unique_gapless_tickets(
    client, make_teacher, make_session
):
    code = await make_session(await make_teacher("join_race"))
    total = 60

    async def one(i: int):
        token = f"{i:02d}".ljust(20, "k")
        return await client.post(f"{API}/{code}/join", json={"name": f"s{i}", "token": token})

    responses = await asyncio.gather(*(one(i) for i in range(total)))
    failed = [r.text for r in responses if r.status_code != 200]
    assert not failed, failed
    assert sorted(r.json()["ticket"] for r in responses) == list(range(1, total + 1))
    assert (await client.get(f"{API}/{code}")).json()["waiting_count"] == total


@pytest.mark.asyncio
async def test_simultaneous_double_click_creates_one_entry(client, make_teacher, make_session):
    code = await make_session(await make_teacher("join_double"))

    async def click():
        return await client.post(f"{API}/{code}/join", json={"name": "Anna", "token": "d" * 20})

    responses = await asyncio.gather(*(click() for _ in range(15)))
    assert all(r.status_code == 200 for r in responses)
    assert {r.json()["ticket"] for r in responses} == {1}
    assert (await client.get(f"{API}/{code}")).json()["waiting_count"] == 1
