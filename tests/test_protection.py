import asyncio

import pytest

from core.config import settings
from core.ratelimit import limiter
from repositories.queue import QueueRepository
from services.queue import QueueService

API = "/api/v1/sessions"


@pytest.fixture(autouse=True)
def fresh_limiter():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def room(make_teacher, make_session):
    async def _room(username: str):
        headers = await make_teacher(username)
        return headers, await make_session(headers)

    return _room


def body(name: str, token: str | None = None) -> dict:
    return {"name": name, "token": token or name.ljust(16, "x")}


@pytest.mark.asyncio
async def test_join_rate_limit_per_ip_returns_429_with_retry_after(client, room, monkeypatch):
    _, code = await room("prot_ip")
    monkeypatch.setattr(settings, "RATE_LIMIT_JOIN_PER_IP_PER_MINUTE", 3)

    statuses = []
    for i in range(5):
        response = await client.post(f"{API}/{code}/join", json=body(f"s{i}"))
        statuses.append(response.status_code)

    assert statuses == [200, 200, 200, 429, 429]
    assert 1 <= int(response.headers["Retry-After"]) <= 60
    error = response.json()
    assert (error["detail"], error["code"]) == ("Too many requests", "rate_limited")


@pytest.mark.asyncio
async def test_entry_token_rate_limit_is_per_token(client, room, monkeypatch):
    _, code = await room("prot_token")
    first = body("Anna")
    second = body("Boris")
    await client.post(f"{API}/{code}/join", json=first)
    await client.post(f"{API}/{code}/join", json=second)
    monkeypatch.setattr(settings, "RATE_LIMIT_TOKEN_PER_MINUTE", 4)

    first_statuses = [
        (
            await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": first["token"]})
        ).status_code
        for _ in range(6)
    ]
    other = await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": second["token"]})

    assert first_statuses == [200, 200, 200, 200, 429, 429]
    assert other.status_code == 200


@pytest.mark.asyncio
async def test_ip_limit_covers_every_endpoint(client, room, monkeypatch):
    _, code = await room("prot_all")
    monkeypatch.setattr(settings, "RATE_LIMIT_IP_PER_MINUTE", 3)
    limiter.reset()

    statuses = [(await client.get(f"{API}/{code}")).status_code for _ in range(5)]

    assert statuses == [200, 200, 200, 429, 429]


@pytest.mark.asyncio
async def test_login_has_a_strict_limit_against_password_guessing(
    client, make_teacher, monkeypatch
):
    await make_teacher("prot_login")
    limiter.reset()
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_PER_IP_PER_MINUTE", 3)

    statuses = []
    for _ in range(5):
        response = await client.post(
            "/api/v1/auth/login", json={"username": "prot_login", "password": "wrong-password"}
        )
        statuses.append(response.status_code)

    assert statuses == [401, 401, 401, 429, 429]


@pytest.mark.asyncio
async def test_rate_limit_can_be_switched_off(client, room, monkeypatch):
    _, code = await room("prot_off")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "RATE_LIMIT_IP_PER_MINUTE", 1)

    statuses = [(await client.get(f"{API}/{code}")).status_code for _ in range(4)]

    assert statuses == [200] * 4


@pytest.mark.asyncio
async def test_rate_limit_window_expires(client, room, monkeypatch):
    _, code = await room("prot_window")
    monkeypatch.setattr(settings, "RATE_LIMIT_IP_PER_MINUTE", 2)
    now = [1000.0]
    monkeypatch.setattr(limiter, "_clock", lambda: now[0])
    limiter.reset()

    assert [(await client.get(f"{API}/{code}")).status_code for _ in range(3)] == [200, 200, 429]
    now[0] += 61
    assert (await client.get(f"{API}/{code}")).status_code == 200


@pytest.mark.asyncio
async def test_too_many_concurrent_requests_get_an_instant_503(client, room, monkeypatch):
    _, code = await room("prot_conc")
    monkeypatch.setattr(settings, "MAX_CONCURRENT_REQUESTS", 3)
    original = QueueService.public_info

    async def slow(self, code_):
        await asyncio.sleep(0.5)
        return await original(self, code_)

    monkeypatch.setattr(QueueService, "public_info", slow)

    started = asyncio.get_running_loop().time()
    responses = await asyncio.gather(*(client.get(f"{API}/{code}") for _ in range(8)))
    statuses = sorted(r.status_code for r in responses)

    assert statuses == [200, 200, 200, 503, 503, 503, 503, 503]
    rejected = next(r for r in responses if r.status_code == 503)
    assert int(rejected.headers["Retry-After"]) >= 1
    error = rejected.json()
    assert (error["detail"], error["code"]) == ("Service is overloaded, retry later", "overloaded")
    assert error["request_id"]
    assert asyncio.get_running_loop().time() - started < 2


@pytest.mark.asyncio
async def test_health_is_exempt_from_the_concurrency_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "MAX_CONCURRENT_REQUESTS", 0)

    assert (await client.get("/health")).status_code == 200


@pytest.mark.asyncio
async def test_name_longer_than_the_limit_is_rejected(client, room, monkeypatch):
    _, code = await room("prot_name")
    monkeypatch.setattr(settings, "NAME_MAX_LENGTH", 10)

    ok = await client.post(f"{API}/{code}/join", json=body("A" * 10))
    long = await client.post(f"{API}/{code}/join", json=body("B" * 11))

    assert (ok.status_code, long.status_code) == (200, 422)


@pytest.mark.asyncio
async def test_name_with_control_characters_is_rejected(client, room):
    _, code = await room("prot_ctrl")

    for bad in ("Anna\x00", "An\x07na", "Anna‮evil"):
        response = await client.post(f"{API}/{code}/join", json=body(bad))
        assert response.status_code == 422, repr(bad)


@pytest.mark.asyncio
async def test_same_token_never_creates_a_second_entry_or_renames(client, room, make_teacher):
    headers, code = await room("prot_dup")
    token = "t" * 20
    results = await asyncio.gather(
        *(
            client.post(f"{API}/{code}/join", json={"name": f"Name{i}", "token": token})
            for i in range(10)
        )
    )

    assert {r.status_code for r in results} == {200}
    assert len({r.json()["ticket"] for r in results}) == 1
    queue = (await client.get(f"{API}/{code}/queue", headers=headers)).json()
    assert len(queue["entries"]) == 1


@pytest.mark.asyncio
async def test_queue_limit_holds_under_concurrent_joins(client, room, monkeypatch):
    headers, code = await room("prot_cap")
    monkeypatch.setattr(settings, "MAX_QUEUE_SIZE", 5)
    original = QueueRepository.queue_counts

    async def slow(self, session_id):
        result = await original(self, session_id)
        await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(QueueRepository, "queue_counts", slow)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)

    responses = await asyncio.gather(
        *(client.post(f"{API}/{code}/join", json=body(f"s{i:02d}")) for i in range(12))
    )

    assert sorted(r.status_code for r in responses) == [200] * 5 + [409] * 7
    queue = (await client.get(f"{API}/{code}/queue", headers=headers)).json()
    assert queue["waiting_count"] == 5


@pytest.mark.asyncio
async def test_total_entries_per_session_is_limited_even_when_students_leave(
    client, room, monkeypatch
):
    headers, code = await room("prot_total")
    monkeypatch.setattr(settings, "MAX_ENTRIES_PER_SESSION", 3)
    for i in range(3):
        student = body(f"s{i}")
        await client.post(f"{API}/{code}/join", json=student)
        await client.post(f"{API}/{code}/leave", headers={"X-Entry-Token": student["token"]})

    response = await client.post(f"{API}/{code}/join", json=body("late"))

    assert response.status_code == 409
    assert response.json()["detail"] == "Session is full"


@pytest.mark.asyncio
async def test_joins_of_one_session_wait_in_memory_not_on_database_connections(
    client, room, monkeypatch
):
    _, code = await room("prot_gate")
    _, other = await room("prot_gate_other")
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    running = {"now": 0, "max": 0}
    original = QueueRepository.get_session

    async def watched(self, code_, *, lock=False):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        try:
            await asyncio.sleep(0.02)
            return await original(self, code_, lock=lock)
        finally:
            running["now"] -= 1

    monkeypatch.setattr(QueueRepository, "get_session", watched)

    await asyncio.gather(
        *(client.post(f"{API}/{code}/join", json=body(f"a{i:02d}")) for i in range(10))
    )
    assert running["max"] == 1

    await asyncio.gather(
        *(
            client.post(f"{API}/{c}/join", json=body(f"b{i:02d}"))
            for i in range(5)
            for c in (code, other)
        )
    )
    assert running["max"] == 2


@pytest.mark.asyncio
async def test_insert_entry_if_room_refuses_when_session_is_full(client, room, db_session):
    _, code = await room("prot_insert")
    repo = QueueRepository(db_session)
    session = await repo.get_session(code)
    assert session is not None

    first = await repo.insert_entry_if_room(session.id, "a", "h1", 1, 1, 10)
    refused_waiting = await repo.insert_entry_if_room(session.id, "b", "h2", 2, 1, 10)
    refused_total = await repo.insert_entry_if_room(session.id, "c", "h3", 3, 10, 1)

    assert first is not None
    assert refused_waiting is None
    assert refused_total is None
