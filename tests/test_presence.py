import asyncio

import pytest
from httpx import ASGITransport, AsyncClient

import services.presence as presence_module
from core.config import settings
from main import app
from services.presence import (
    AttemptGuard,
    code_for,
    current_code,
    is_valid,
    seconds_left,
    window_index,
)

API = "/api/v1"
S = f"{API}/sessions"
SECRET = "unit-test-secret-unit-test-secret-1234"


def test_codes_have_six_digits_and_are_stable_inside_a_window():
    first = current_code(SECRET, "s1", 30, 1000.0)
    assert len(first) == 6 and first.isdigit()
    assert first == current_code(SECRET, "s1", 30, 1000.0 + 5)
    assert first == code_for(SECRET, "s1", window_index(1000.0, 30))


def test_codes_differ_between_windows_sessions_and_secrets():
    codes = {current_code(SECRET, "s1", 30, 30.0 * n) for n in range(50)}
    assert len(codes) >= 48
    assert current_code(SECRET, "s1", 30, 0) != current_code(SECRET, "s2", 30, 0)
    assert current_code(SECRET, "s1", 30, 0) != current_code("other" * 8, "s1", 30, 0)


def test_the_current_and_the_previous_code_are_accepted_and_older_or_future_are_not():
    now = 3000.0
    current = code_for(SECRET, "s1", window_index(now, 30))
    previous = code_for(SECRET, "s1", window_index(now, 30) - 1)
    older = code_for(SECRET, "s1", window_index(now, 30) - 2)
    future = code_for(SECRET, "s1", window_index(now, 30) + 1)
    assert is_valid(SECRET, "s1", 30, now, current)
    assert is_valid(SECRET, "s1", 30, now, previous)
    assert not is_valid(SECRET, "s1", 30, now, older)
    assert not is_valid(SECRET, "s1", 30, now, future)


@pytest.mark.parametrize("bad", ["", "12345", "1234567", "12 456", "abcdef", "١٢٣٤٥٦", "12345\n"])
def test_malformed_codes_are_rejected_without_errors(bad):
    assert not is_valid(SECRET, "s1", 30, 1000.0, bad)


def test_seconds_left_counts_down_inside_the_window():
    assert seconds_left(30, 0.0) == 30
    assert seconds_left(30, 29.5) == 1
    assert seconds_left(60, 61.0) == 59
    assert all(1 <= seconds_left(45, float(t)) <= 45 for t in range(200))


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_a_few_failures_from_one_address_lock_only_that_address():
    clock = Clock()
    guard = AttemptGuard(ip_failures=3, session_failures=100, clock=clock)
    for _ in range(2):
        guard.failed("S", "1.1.1.1")
    assert guard.retry_after("S", "1.1.1.1") == 0
    guard.failed("S", "1.1.1.1")
    assert guard.retry_after("S", "1.1.1.1") > 0
    assert guard.retry_after("S", "2.2.2.2") == 0
    assert guard.retry_after("OTHER", "1.1.1.1") == 0


def test_failures_from_many_addresses_lock_the_whole_session():
    guard = AttemptGuard(ip_failures=5, session_failures=6, clock=Clock())
    for i in range(6):
        guard.failed("S", f"10.0.0.{i}")
    assert guard.retry_after("S", "10.9.9.9") > 0
    assert guard.retry_after("T", "10.9.9.9") == 0


def test_old_failures_are_forgotten_and_locks_expire():
    clock = Clock()
    guard = AttemptGuard(ip_failures=3, window_seconds=60, lock_seconds=120, clock=clock)
    guard.failed("S", "a")
    guard.failed("S", "a")
    clock.now = 61
    guard.failed("S", "a")
    assert guard.retry_after("S", "a") == 0
    guard.failed("S", "a")
    guard.failed("S", "a")
    assert 0 < guard.retry_after("S", "a") <= 121
    clock.now = 61 + 119
    assert guard.retry_after("S", "a") > 0
    clock.now = 61 + 122
    assert guard.retry_after("S", "a") == 0


def test_reset_clears_everything():
    guard = AttemptGuard(ip_failures=1, session_failures=1, clock=Clock())
    guard.failed("S", "a")
    assert guard.retry_after("S", "a") > 0
    guard.reset()
    assert guard.retry_after("S", "a") == 0


def test_a_huge_number_of_distinct_keys_is_pruned():
    clock = Clock()
    guard = AttemptGuard(ip_failures=5, window_seconds=10, lock_seconds=10, clock=clock)
    for i in range(20050):
        guard.failed(f"S{i}", "a")
    clock.now = 1000
    guard.failed("final", "a")
    assert len(guard._ip) < 20000


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    fake.now = 100_000.0
    monkeypatch.setattr(presence_module, "wall_clock", fake)
    return fake


@pytest.fixture
def room(client, make_teacher):
    async def _room(username: str, **extra):
        headers = await make_teacher(username)
        response = await client.post(S, headers=headers, json={"title": "Сдача", **extra})
        assert response.status_code == 201, response.text
        return headers, response.json()

    return _room


async def join(client, code, name, presence_code=None, ip_client=None):
    body = {"name": name, "token": name.encode().hex().ljust(16, "x")}
    if presence_code is not None:
        body["presence_code"] = presence_code
    return await (ip_client or client).post(f"{S}/{code}/join", json=body)


async def shown(client, headers, code):
    return (await client.get(f"{S}/{code}/presence", headers=headers)).json()


@pytest.mark.asyncio
async def test_the_check_is_off_by_default_and_joining_works_as_before(client, room):
    headers, session = await room("pr_off")
    assert (session["presence_check"], session["presence_interval"]) == ("off", 30)
    assert (await join(client, session["code"], "Анна")).status_code == 200
    assert (await join(client, session["code"], "Борис", "000000")).status_code == 200
    info = await shown(client, headers, session["code"])
    assert info == {"enabled": False, "interval": 30, "code": None, "expires_in": None}


@pytest.mark.asyncio
async def test_joining_needs_the_code_shown_on_the_screen(client, room, clock):
    headers, session = await room("pr_on", presence_check="rotating_code")
    code = session["code"]
    info = await shown(client, headers, code)
    assert info["enabled"] is True and len(info["code"]) == 6 and 1 <= info["expires_in"] <= 30
    missing = await join(client, code, "Анна")
    assert missing.status_code == 403 and missing.json()["code"] == "presence_code_required"
    wrong = await join(client, code, "Анна", "000000" if info["code"] != "000000" else "111111")
    assert wrong.status_code == 403 and wrong.json()["code"] == "presence_code_invalid"
    assert wrong.json()["detail"] == "Неверный код, посмотрите на экран"
    right = await join(client, code, "Анна", info["code"])
    assert right.status_code == 200 and right.json()["position"] == 1


@pytest.mark.asyncio
async def test_spaces_and_dashes_in_the_typed_code_are_ignored(client, room, clock):
    headers, session = await room("pr_fmt", presence_check="rotating_code")
    info = await shown(client, headers, session["code"])
    typed = f" {info['code'][:3]}-{info['code'][3:]} "
    assert (await join(client, session["code"], "Анна", typed)).status_code == 200


@pytest.mark.asyncio
async def test_the_previous_code_still_works_but_an_expired_one_does_not(client, room, clock):
    headers, session = await room("pr_prev", presence_check="rotating_code")
    code = session["code"]
    first = (await shown(client, headers, code))["code"]
    clock.now += 31
    second = (await shown(client, headers, code))["code"]
    assert second != first
    assert (await join(client, code, "Анна", first)).status_code == 200
    clock.now += 31
    third = (await shown(client, headers, code))["code"]
    assert third not in (first, second)
    expired = await join(client, code, "Борис", first)
    assert expired.status_code == 403 and expired.json()["code"] == "presence_code_invalid"
    assert (await join(client, code, "Борис", second)).status_code == 200
    assert (await join(client, code, "Вера", third)).status_code == 200


@pytest.mark.asyncio
async def test_a_student_already_in_the_queue_is_not_asked_again(client, room, clock):
    headers, session = await room("pr_again", presence_check="rotating_code")
    code = session["code"]
    info = await shown(client, headers, code)
    assert (await join(client, code, "Анна", info["code"])).status_code == 200
    clock.now += 600
    again = await join(client, code, "Анна")
    assert again.status_code == 200 and again.json()["ticket"] == 1


@pytest.mark.asyncio
async def test_turning_the_check_on_or_off_does_not_touch_students_already_in(client, room, clock):
    headers, session = await room("pr_toggle")
    code = session["code"]
    assert (await join(client, code, "Анна")).status_code == 200
    on = await client.put(f"{S}/{code}/presence", headers=headers, json={"mode": "rotating_code"})
    assert on.status_code == 200 and on.json()["enabled"] is True
    assert (await join(client, code, "Борис")).status_code == 403
    assert (await join(client, code, "Анна")).status_code == 200
    off = await client.put(f"{S}/{code}/presence", headers=headers, json={"mode": "off"})
    assert off.json() == {"enabled": False, "interval": 30, "code": None, "expires_in": None}
    assert (await join(client, code, "Борис")).status_code == 200
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    assert [e["name"] for e in queue["entries"]] == ["Анна", "Борис"]


@pytest.mark.asyncio
async def test_the_interval_is_configurable_between_30_and_60_seconds(client, room, clock):
    headers, session = await room(
        "pr_interval", presence_check="rotating_code", presence_interval=60
    )
    code = session["code"]
    assert (await shown(client, headers, code))["interval"] == 60
    first = (await shown(client, headers, code))["code"]
    clock.now = 60.0 * 1000
    same_window = (await shown(client, headers, code))["code"]
    clock.now += 59
    assert (await shown(client, headers, code))["code"] == same_window
    clock.now += 2
    assert (await shown(client, headers, code))["code"] != same_window
    assert first
    changed = await client.put(
        f"{S}/{code}/presence", headers=headers, json={"mode": "rotating_code", "interval": 45}
    )
    assert changed.json()["interval"] == 45
    for bad in (29, 61, 0):
        refused = await client.put(
            f"{S}/{code}/presence", headers=headers, json={"mode": "rotating_code", "interval": bad}
        )
        assert refused.status_code == 422
    too_small = await client.post(S, headers=headers, json={"title": "X", "presence_interval": 10})
    assert too_small.status_code == 422
    bad_mode = await client.put(f"{S}/{code}/presence", headers=headers, json={"mode": "sms"})
    assert bad_mode.status_code == 422


@pytest.mark.asyncio
async def test_only_the_owner_can_see_the_code_or_change_the_check(
    client, room, make_teacher, clock
):
    headers, session = await room("pr_owner", presence_check="rotating_code")
    code = session["code"]
    other = await make_teacher("pr_owner_other")
    assert (await client.get(f"{S}/{code}/presence", headers=other)).status_code == 404
    assert (
        await client.put(f"{S}/{code}/presence", headers=other, json={"mode": "off"})
    ).status_code == 404
    assert (await client.get(f"{S}/{code}/presence")).status_code == 401
    assert (await client.put(f"{S}/{code}/presence", json={"mode": "off"})).status_code == 401
    assert (await shown(client, headers, code))["enabled"] is True
    public = (await client.get(f"{S}/{code}")).json()
    assert public["presence_check"] == "rotating_code"
    assert (await shown(client, headers, code))["code"] not in str(public)


@pytest.mark.asyncio
async def test_the_code_of_one_session_does_not_open_another(client, room, clock):
    headers, first = await room("pr_two", presence_check="rotating_code")
    second = (
        await client.post(
            S, headers=headers, json={"title": "Вторая", "presence_check": "rotating_code"}
        )
    ).json()
    code_one = (await shown(client, headers, first["code"]))["code"]
    code_two = (await shown(client, headers, second["code"]))["code"]
    assert code_one != code_two
    assert (await join(client, second["code"], "Анна", code_one)).status_code == 403
    assert (await join(client, second["code"], "Анна", code_two)).status_code == 200


@pytest.mark.asyncio
async def test_guessing_is_stopped_after_a_few_wrong_codes_from_one_address(client, room, clock):
    headers, session = await room("pr_guess", presence_check="rotating_code")
    code = session["code"]
    right = (await shown(client, headers, code))["code"]
    wrong = "000001" if right != "000001" else "000002"
    statuses = [
        (await join(client, code, f"Гость {i}", wrong)).status_code
        for i in range(settings.PRESENCE_IP_FAILURES)
    ]
    assert statuses == [403] * settings.PRESENCE_IP_FAILURES
    locked = await join(client, code, "Гость X", right)
    assert locked.status_code == 429 and locked.json()["code"] == "presence_locked"
    assert int(locked.headers["Retry-After"]) > 0
    assert "Подождите" in locked.json()["detail"]


@pytest.mark.asyncio
async def test_another_address_is_not_blocked_by_someone_elses_guessing(client, room, clock):
    headers, session = await room("pr_ips", presence_check="rotating_code")
    code = session["code"]
    right = (await shown(client, headers, code))["code"]
    wrong = "000001" if right != "000001" else "000002"
    for i in range(settings.PRESENCE_IP_FAILURES):
        await join(client, code, f"Гость {i}", wrong)
    assert (await join(client, code, "Заперт", right)).status_code == 429
    async with AsyncClient(
        transport=ASGITransport(app=app, client=("203.0.113.7", 5555)), base_url="http://test"
    ) as other_ip:
        ok = await join(client, code, "Честный", right, ip_client=other_ip)
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_many_addresses_together_lock_the_whole_session_for_a_while(client, room, clock):
    headers, session = await room("pr_swarm", presence_check="rotating_code")
    code = session["code"]
    right = (await shown(client, headers, code))["code"]
    wrong = "000001" if right != "000001" else "000002"
    for i in range(settings.PRESENCE_SESSION_FAILURES):
        async with AsyncClient(
            transport=ASGITransport(app=app, client=(f"198.51.100.{i % 250}", 4000 + i)),
            base_url="http://test",
        ) as source:
            await join(client, code, f"Гость {i}", wrong, ip_client=source)
    blocked = await join(client, code, "Честный", right)
    assert blocked.status_code == 429 and blocked.json()["code"] == "presence_locked"
    presence_module.guard.reset()
    assert (await join(client, code, "Честный", right)).status_code == 200


@pytest.mark.asyncio
async def test_concurrent_wrong_guesses_never_get_through_and_never_crash(client, room, clock):
    headers, session = await room("pr_burst", presence_check="rotating_code")
    code = session["code"]
    right = (await shown(client, headers, code))["code"]
    guesses = [f"{n:06d}" for n in range(40) if f"{n:06d}" != right]
    results = await asyncio.gather(
        *(join(client, code, f"Гость {n}", guess) for n, guess in enumerate(guesses))
    )
    codes = [r.status_code for r in results]
    assert set(codes) <= {403, 429}
    assert codes.count(403) <= settings.PRESENCE_IP_FAILURES
    assert 429 in codes
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    assert queue["entries"] == []


@pytest.mark.asyncio
async def test_joins_racing_a_code_change_accept_the_current_or_the_previous_code(
    client, room, clock
):
    headers, session = await room("pr_edge", presence_check="rotating_code")
    code = session["code"]
    clock.now = 30.0 * 5000 + 29.9
    old = (await shown(client, headers, code))["code"]

    async def late(n):
        clock.now = 30.0 * 5001 + 0.1 * n
        return await join(client, code, f"Студент {n}", old)

    results = await asyncio.gather(*(late(n) for n in range(8)))
    assert [r.status_code for r in results] == [200] * 8
    clock.now = 30.0 * 5002 + 1
    stale = await join(client, code, "Опоздавший", old)
    assert stale.status_code == 403


@pytest.mark.asyncio
async def test_toggling_the_check_while_students_join_leaves_consistent_data(client, room, clock):
    headers, session = await room("pr_flip")
    code = session["code"]

    async def flip():
        for mode in ("rotating_code", "off", "rotating_code", "off"):
            await client.put(f"{S}/{code}/presence", headers=headers, json={"mode": mode})

    async def joiner(n):
        return await join(client, code, f"Студент {n}", "000000")

    results = await asyncio.gather(flip(), *(joiner(n) for n in range(10)))
    assert {r.status_code for r in results[1:]} <= {200, 403, 429}
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    admitted = sum(1 for r in results[1:] if r.status_code == 200)
    assert len(queue["entries"]) == admitted


@pytest.mark.asyncio
async def test_a_closed_session_cannot_change_the_check(client, room):
    headers, session = await room("pr_closed")
    await client.post(f"{S}/{session['code']}/close", headers=headers)
    response = await client.put(
        f"{S}/{session['code']}/presence", headers=headers, json={"mode": "rotating_code"}
    )
    assert response.status_code == 409 and response.json()["code"] == "session_closed"


@pytest.mark.asyncio
async def test_the_check_works_together_with_the_lottery_pool(client, room, clock):
    headers, session = await room("pr_pool", presence_check="rotating_code", order_mode="lottery")
    code = session["code"]
    info = await shown(client, headers, code)
    assert (await join(client, code, "Анна")).status_code == 403
    pooled = await join(client, code, "Анна", info["code"])
    assert pooled.status_code == 200 and pooled.json()["in_pool"] is True


@pytest.mark.asyncio
async def test_the_code_response_is_never_cached(client, room, clock):
    headers, session = await room("pr_cache", presence_check="rotating_code")
    response = await client.get(f"{S}/{session['code']}/presence", headers=headers)
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.asyncio
async def test_default_limits_are_sane():
    assert 1 <= settings.PRESENCE_IP_FAILURES <= settings.PRESENCE_SESSION_FAILURES
    assert settings.PRESENCE_LOCK_SECONDS >= settings.PRESENCE_FAILURE_WINDOW_SECONDS
