import asyncio
import json
import time

import pytest
import pytest_asyncio

from core.config import settings
from core.events import notify_changed
from core.exceptions import OverloadedError
from services import live
from services.live import hub, student_stream, teacher_stream
from tests.conftest import engine

API = "/api/v1/sessions"


async def event(stream, timeout: float = 3.0) -> tuple[str, dict]:
    while True:
        chunk = await asyncio.wait_for(stream.__anext__(), timeout)
        if chunk.startswith(":"):
            continue
        lines = chunk.strip().split("\n")
        name = lines[0].removeprefix("event: ")
        return name, json.loads(lines[1].removeprefix("data: "))


async def raw(stream, timeout: float = 3.0) -> str:
    return await asyncio.wait_for(stream.__anext__(), timeout)


async def entries(client, headers, code):
    return (await client.get(f"{API}/{code}/queue", headers=headers)).json()["entries"]


async def entry_id(client, headers, code, name):
    return next(e["id"] for e in await entries(client, headers, code) if e["name"] == name)


async def act(client, headers, code, action, entry=None):
    suffix = f"entries/{entry}/{action}" if entry else action
    return await client.post(f"{API}/{code}/{suffix}", headers=headers)


async def my(client, code, student):
    return (
        await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": student["token"]})
    ).json()


@pytest.fixture
def room(make_teacher, make_session, join):
    async def _room(username: str, people: tuple[str, ...]):
        headers = await make_teacher(username)
        code = await make_session(headers)
        students = {name: await join(code, name) for name in people}
        return headers, code, students

    return _room


@pytest_asyncio.fixture(autouse=True)
async def clean_hub():
    yield
    await hub.close()
    hub._states.clear()


@pytest.mark.asyncio
async def test_first_event_matches_the_polling_endpoint(client, room):
    headers, code, students = await room("live_first", ("A", "B", "C", "D"))
    await act(client, headers, code, "promote", await entry_id(client, headers, code, "D"))
    await act(client, headers, code, "next")

    for name, student in students.items():
        eid = await entry_id(client, headers, code, name)
        stream = student_stream(code, eid)
        _, data = await event(stream)
        assert data == await my(client, code, student), name
        await stream.aclose()


@pytest.mark.asyncio
async def test_students_get_updates_after_teacher_actions(client, room):
    headers, code, students = await room("live_updates", ("A", "B", "C"))
    a_id = await entry_id(client, headers, code, "A")
    b_id = await entry_id(client, headers, code, "B")
    stream_a, stream_b = student_stream(code, a_id), student_stream(code, b_id)
    assert (await event(stream_a))[1]["position"] == 1
    assert (await event(stream_b))[1]["position"] == 2

    await act(client, headers, code, "next")

    _, a_now = await event(stream_a)
    _, b_now = await event(stream_b)
    assert a_now["status"] == "called"
    assert (b_now["position"], b_now["people_ahead"]) == (1, 1)
    assert b_now["now_serving"]["name"] == "A"

    await act(client, headers, code, "done", a_id)
    _, a_done = await event(stream_a)
    assert a_done["status"] == "done"
    assert (await event(stream_a))[0] == "end"
    with pytest.raises(StopAsyncIteration):
        await stream_a.__anext__()
    _, b_after = await event(stream_b)
    assert b_after["now_serving"] is None
    await stream_b.aclose()


@pytest.mark.asyncio
async def test_teacher_stream_matches_queue_and_follows_joins(client, room, join):
    headers, code, _ = await room("live_teacher", ("A",))
    stream = teacher_stream(code)
    _, first = await event(stream)
    assert first == (await client.get(f"{API}/{code}/queue", headers=headers)).json()

    await join(code, "B")
    _, second = await event(stream)
    assert [e["name"] for e in second["entries"]] == ["A", "B"]
    assert second["waiting_count"] == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_client_disconnect_releases_the_subscription(client, room):
    headers, code, _ = await room("live_disconnect", ("A",))
    stream = student_stream(code, await entry_id(client, headers, code, "A"))
    await event(stream)
    assert hub.count(code) == 1

    await stream.aclose()

    assert hub.count(code) == 0
    assert hub.snapshot(code) is None


@pytest.mark.asyncio
async def test_idle_streams_do_not_hold_database_connections(client, room):
    headers, code, _ = await room("live_pool", tuple(f"s{i}" for i in range(30)))
    streams = []
    for entry in await entries(client, headers, code):
        stream = student_stream(code, entry["id"])
        await event(stream)
        streams.append(stream)

    assert hub.count(code) == 30
    assert engine.pool.checkedout() == 0

    for stream in streams:
        await stream.aclose()


@pytest.mark.asyncio
async def test_heartbeat_is_sent_when_nothing_changes(client, room):
    headers, code, _ = await room("live_heartbeat", ("A",))
    stream = student_stream(code, await entry_id(client, headers, code, "A"), heartbeat=0.05)
    await event(stream)

    assert await raw(stream) == ": ping\n\n"
    await stream.aclose()


@pytest.mark.asyncio
async def test_connection_limit_per_session(client, room, monkeypatch):
    headers, code, _ = await room("live_limit", ("A", "B", "C"))
    monkeypatch.setattr(hub, "_max", 2)
    ids = [e["id"] for e in await entries(client, headers, code)]
    streams = [student_stream(code, i) for i in ids[:2]]
    for stream in streams:
        await event(stream)

    with pytest.raises(OverloadedError):
        hub.check_capacity(code)
    third = student_stream(code, ids[2])
    assert (await event(third))[0] == "end"
    assert hub.count(code) == 2
    assert hub.rejected >= 1
    for stream in streams:
        await stream.aclose()


def count_reads(monkeypatch, code):
    reads = []
    original = hub._loader

    async def counting(requested):
        if requested == code:
            reads.append(requested)
        return await original(requested)

    monkeypatch.setattr(hub, "_loader", counting)
    return reads


@pytest.mark.asyncio
async def test_burst_of_changes_is_coalesced_into_few_database_reads(client, room, monkeypatch):
    headers, code, students = await room("live_burst", ("A",))
    reads = count_reads(monkeypatch, code)
    monkeypatch.setattr(hub, "_coalesce", 0.5)
    stream = student_stream(code, await entry_id(client, headers, code, "A"))
    await event(stream)
    reads.clear()

    for _ in range(200):
        notify_changed(code)
    await asyncio.sleep(1.5)
    assert len(reads) == 1

    for _ in range(200):
        notify_changed(code)
    await asyncio.sleep(1.5)
    assert len(reads) == 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_a_burst_of_real_joins_ends_with_a_fresh_snapshot_and_bounded_reads(
    client, room, join, monkeypatch
):
    headers, code, students = await room("live_burst_joins", ("A",))
    reads = count_reads(monkeypatch, code)
    stream = student_stream(code, await entry_id(client, headers, code, "A"))
    await event(stream)
    reads.clear()

    started = time.monotonic()
    for i in range(20):
        await join(code, f"late{i}")
    fresh = False
    for _ in range(400):
        snapshot = hub.snapshot(code)
        if snapshot is not None and any(e.name == "late19" for e in snapshot.entries):
            fresh = True
            break
        await asyncio.sleep(0.05)
    elapsed = time.monotonic() - started

    assert fresh
    assert 1 <= len(reads) <= elapsed / settings.SSE_COALESCE_SECONDS + 2
    assert (await entries(client, headers, code))[-1]["name"] == "late19"
    await stream.aclose()


@pytest.mark.asyncio
async def test_300_subscribers_cost_one_database_read_per_change(client, room, join):
    headers, code, students = await room("live_300", ("A",))
    snapshot_ids = []
    for i in range(299):
        await join(code, f"s{i:03d}")
    snapshot_ids = [e["id"] for e in await entries(client, headers, code)]
    streams = [student_stream(code, i) for i in snapshot_ids]
    first = [await event(s) for s in streams]
    assert hub.count(code) == 300
    assert first[299][1]["position"] == 300
    before = hub.refreshes

    await act(client, headers, code, "next")
    updates = [await event(s) for s in streams]

    assert hub.refreshes - before == 1
    assert updates[0][1]["status"] == "called"
    assert updates[1][1]["position"] == 1
    assert updates[299][1]["position"] == 299
    for stream in streams:
        await stream.aclose()


@pytest.mark.asyncio
async def test_stream_of_a_student_missing_from_a_stale_snapshot_stays_open(
    client, room, join, monkeypatch
):
    headers, code, students = await room("live_stale", ("A",))
    watcher = teacher_stream(code)
    await event(watcher)
    stale = hub.snapshot(code)
    fresh_loader = hub._loader

    async def stale_loader(requested):
        return stale

    monkeypatch.setattr(hub, "_loader", stale_loader)
    late = await join(code, "Late")
    eid = await entry_id(client, headers, code, "Late")

    stream = student_stream(code, eid)
    name, data = await event(stream)
    assert (name, data["status"]) == ("entry", "waiting")
    pending = asyncio.ensure_future(stream.__anext__())
    done, _ = await asyncio.wait({pending}, timeout=0.6)
    assert not done

    monkeypatch.setattr(hub, "_loader", fresh_loader)
    await client.post(f"{API}/{code}/leave", headers={"X-Entry-Token": students["A"]["token"]})
    chunk = await asyncio.wait_for(pending, 3.0)
    head, body = chunk.strip().splitlines()[:2]
    assert head == "event: entry"
    assert json.loads(body.removeprefix("data: "))["position"] == 1
    assert late["status"] == "waiting"
    await stream.aclose()
    await watcher.aclose()


@pytest.mark.asyncio
async def test_student_who_already_left_gets_the_final_state_and_the_stream_ends(client, room):
    headers, code, students = await room("live_left", ("A", "B"))
    eid = await entry_id(client, headers, code, "A")
    await client.post(f"{API}/{code}/leave", headers={"X-Entry-Token": students["A"]["token"]})

    stream = student_stream(code, eid)
    name, data = await event(stream)
    assert (name, data["status"]) == ("entry", "left")
    assert (await event(stream))[0] == "end"
    with pytest.raises(StopAsyncIteration):
        await stream.__anext__()
    assert hub.count(code) == 0


@pytest.mark.asyncio
async def test_server_shutdown_ends_open_streams(client, room):
    headers, code, _ = await room("live_shutdown", ("A",))
    stream = student_stream(code, await entry_id(client, headers, code, "A"))
    await event(stream)

    await hub.close()

    name, data = await event(stream)
    assert (name, data) == ("end", {"reason": "server shutdown"})


@pytest.mark.asyncio
async def test_failed_refresh_is_retried_and_stream_recovers(client, room, monkeypatch):
    headers, code, _ = await room("live_retry", ("A", "B"))
    stream = student_stream(code, await entry_id(client, headers, code, "B"))
    assert (await event(stream))[1]["position"] == 2

    original = live.gateway.load_snapshot
    calls = {"n": 0}

    async def flaky(code_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("db hiccup")
        return await original(code_)

    monkeypatch.setattr(hub, "_loader", flaky)
    monkeypatch.setattr(live.settings, "SSE_COALESCE_SECONDS", 0.01)
    await act(client, headers, code, "next")

    _, data = await event(stream, timeout=5)
    assert data["position"] == 1
    assert calls["n"] >= 2
    await stream.aclose()


@pytest.mark.asyncio
async def test_endpoint_rejects_unknown_student_and_foreign_teacher(client, make_teacher, room):
    headers, code, _ = await room("live_auth", ("A",))
    stranger = await make_teacher("live_auth_other")

    unknown = await client.get(f"{API}/{code}/events", headers={"X-Entry-Token": "z" * 20})
    foreign = await client.get(f"{API}/{code}/queue/events", headers=stranger)
    missing = await client.get(f"{API}/NOPE00/events", headers={"X-Entry-Token": "z" * 20})
    anonymous = await client.get(f"{API}/{code}/queue/events")

    assert [r.status_code for r in (unknown, foreign, missing, anonymous)] == [404, 404, 404, 401]


@pytest.mark.asyncio
async def test_endpoint_answers_503_with_retry_after_when_the_limit_is_reached(
    client, room, monkeypatch
):
    headers, code, students = await room("live_503", ("A",))
    monkeypatch.setattr(hub, "_max", 0)

    response = await client.get(
        f"{API}/{code}/events", headers={"X-Entry-Token": students["A"]["token"]}
    )

    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_endpoint_can_be_disabled(client, room, monkeypatch):
    headers, code, students = await room("live_off", ("A",))
    monkeypatch.setattr(settings, "SSE_ENABLED", False)

    response = await client.get(
        f"{API}/{code}/events", headers={"X-Entry-Token": students["A"]["token"]}
    )

    assert response.status_code == 404
