import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from models.entry import EntryORM
from repositories.queue import QueueRepository

API = "/api/v1/sessions"


async def queue(client, headers, code):
    return (await client.get(f"{API}/{code}/queue", headers=headers)).json()


async def entry_id(client, headers, code, name):
    entries = (await queue(client, headers, code))["entries"]
    return next(e["id"] for e in entries if e["name"] == name)


async def post(client, headers, code, action, eid=None):
    suffix = f"entries/{eid}/{action}" if eid else action
    return await client.post(f"{API}/{code}/{suffix}", headers=headers)


@pytest.mark.asyncio
async def test_call_next_follows_ticket_order_and_requires_finishing(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_order")
    code = await make_session(headers)
    await join(code, "Anna")
    await join(code, "Boris")

    called = await post(client, headers, code, "next")
    assert called.status_code == 200
    assert (called.json()["name"], called.json()["status"]) == ("Anna", "called")
    assert (await post(client, headers, code, "next")).status_code == 409

    info = (await client.get(f"{API}/{code}")).json()
    assert info["now_serving"] == {"ticket": 1, "name": None}
    assert info["waiting_count"] == 1

    done = await post(client, headers, code, "done", called.json()["id"])
    assert done.json()["status"] == "done"
    nxt = await post(client, headers, code, "next")
    assert nxt.json()["name"] == "Boris"
    assert (await post(client, headers, code, "done", nxt.json()["id"])).status_code == 200
    assert (await post(client, headers, code, "next")).status_code == 409


@pytest.mark.asyncio
async def test_student_sees_called_status_and_eta_accounts_for_current(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_student_view")
    code = await make_session(headers)
    anna = await join(code, "Anna")
    boris = await join(code, "Boris")
    await post(client, headers, code, "next")

    me_a = (await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": anna["token"]})).json()
    assert (me_a["status"], me_a["position"], me_a["eta_seconds"]) == ("called", 0, 0)
    me_b = (await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": boris["token"]})).json()
    assert (me_b["status"], me_b["position"], me_b["people_ahead"], me_b["eta_seconds"]) == (
        "waiting",
        1,
        1,
        300,
    )


@pytest.mark.asyncio
async def test_concurrent_call_next_calls_exactly_one_student(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_race_next")
    code = await make_session(headers)
    for i in range(5):
        await join(code, f"s{i}")

    responses = await asyncio.gather(*(post(client, headers, code, "next") for _ in range(10)))
    assert sorted(r.status_code for r in responses) == [200] + [409] * 9
    entries = (await queue(client, headers, code))["entries"]
    assert len([e for e in entries if e["status"] == "called"]) == 1


@pytest.mark.asyncio
async def test_done_is_idempotent_and_requires_called(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_done")
    code = await make_session(headers)
    await join(code, "Anna")
    eid = await entry_id(client, headers, code, "Anna")

    assert (await post(client, headers, code, "done", eid)).status_code == 409
    await post(client, headers, code, "next")
    first = await post(client, headers, code, "done", eid)
    second = await post(client, headers, code, "done", eid)
    assert first.status_code == second.status_code == 200
    assert second.json()["status"] == "done"


@pytest.mark.asyncio
async def test_average_service_time_adapts(client, make_teacher, make_session, join, db_session):
    headers = await make_teacher("flow_avg")
    code = await make_session(headers)
    await join(code, "Anna")
    await post(client, headers, code, "next")
    eid = await entry_id(client, headers, code, "Anna")
    await db_session.execute(
        update(EntryORM)
        .where(EntryORM.id == eid)
        .values(called_at=EntryORM.called_at - timedelta(seconds=100))
    )
    await db_session.commit()

    await post(client, headers, code, "done", eid)
    assert (await queue(client, headers, code))["avg_service_seconds"] == round(
        0.7 * 300 + 0.3 * 100
    )


@pytest.mark.asyncio
async def test_skip_and_requeue_goes_to_the_end(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_skip")
    code = await make_session(headers)
    for name in ("Anna", "Boris", "Clara"):
        await join(code, name)
    anna = await entry_id(client, headers, code, "Anna")

    await post(client, headers, code, "next")
    skipped = await post(client, headers, code, "skip", anna)
    assert skipped.json()["status"] == "skipped"
    assert (await post(client, headers, code, "skip", anna)).status_code == 200

    back = await post(client, headers, code, "requeue", anna)
    assert back.json()["status"] == "waiting"
    assert back.json()["ticket"] == 1

    order = []
    for _ in range(3):
        called = (await post(client, headers, code, "next")).json()
        order.append(called["name"])
        await post(client, headers, code, "done", called["id"])
    assert order == ["Boris", "Clara", "Anna"]


@pytest.mark.asyncio
async def test_requeue_only_for_skipped(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_requeue")
    code = await make_session(headers)
    await join(code, "Anna")
    await post(client, headers, code, "next")
    eid = await entry_id(client, headers, code, "Anna")
    assert (await post(client, headers, code, "requeue", eid)).status_code == 409


@pytest.mark.asyncio
async def test_promote_moves_student_to_the_front(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_promote")
    code = await make_session(headers)
    await join(code, "Anna")
    await join(code, "Boris")
    clara = await join(code, "Clara")
    cid = await entry_id(client, headers, code, "Clara")

    assert (await post(client, headers, code, "promote", cid)).status_code == 200
    me = (await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": clara["token"]})).json()
    assert me["position"] == 1
    assert (await post(client, headers, code, "promote", cid)).status_code == 200

    first = (await post(client, headers, code, "next")).json()
    assert first["name"] == "Clara"
    assert (await post(client, headers, code, "promote", cid)).status_code == 409


@pytest.mark.asyncio
async def test_student_can_leave_and_rejoin_with_new_token(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_leave")
    code = await make_session(headers)
    anna = await join(code, "Anna")
    await join(code, "Boris")
    token = {"X-Entry-Token": anna["token"]}

    assert (await client.post(f"{API}/{code}/leave", headers=token)).json()["status"] == "left"
    assert (await client.post(f"{API}/{code}/leave", headers=token)).json()["status"] == "left"
    assert (await post(client, headers, code, "next")).json()["name"] == "Boris"

    rejoined = await join(code, "Anna", "n" * 20)
    assert (rejoined["status"], rejoined["ticket"]) == ("waiting", 3)


@pytest.mark.asyncio
async def test_called_student_can_leave_and_teacher_can_continue(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_leave_called")
    code = await make_session(headers)
    anna = await join(code, "Anna")
    await join(code, "Boris")
    await post(client, headers, code, "next")
    await client.post(f"{API}/{code}/leave", headers={"X-Entry-Token": anna["token"]})
    assert (await post(client, headers, code, "next")).json()["name"] == "Boris"


@pytest.mark.asyncio
async def test_student_token_is_required_and_scoped(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_token")
    code = await make_session(headers)
    other = await make_session(headers, "Other")
    anna = await join(code, "Anna")

    assert (await client.get(f"{API}/{code}/me")).status_code == 401
    wrong = await client.get(f"{API}/{code}/me", headers={"X-Entry-Token": "w" * 20})
    assert wrong.status_code == 404
    cross = await client.get(f"{API}/{other}/me", headers={"X-Entry-Token": anna["token"]})
    assert cross.status_code == 404
    assert (await client.post(f"{API}/{code}/leave")).status_code == 401


@pytest.mark.asyncio
async def test_teacher_cannot_touch_someone_elses_session(client, make_teacher, make_session, join):
    owner = await make_teacher("flow_owner")
    intruder = await make_teacher("flow_intruder")
    code = await make_session(owner)
    await join(code, "Anna")
    eid = await entry_id(client, owner, code, "Anna")

    assert (await client.get(f"{API}/{code}/queue", headers=intruder)).status_code == 404
    for action in ("next", "close"):
        assert (await post(client, intruder, code, action)).status_code == 404
    for action in ("done", "skip", "promote", "requeue"):
        assert (await post(client, intruder, code, action, eid)).status_code == 404

    assert (await client.get(API, headers=intruder)).json() == []
    assert len((await client.get(API, headers=owner)).json()) == 1


@pytest.mark.asyncio
async def test_entry_from_another_session_is_not_addressable(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_cross")
    first = await make_session(headers, "A")
    second = await make_session(headers, "B")
    await join(first, "Anna")
    eid = await entry_id(client, headers, first, "Anna")
    assert (await post(client, headers, second, "skip", eid)).status_code == 404


@pytest.mark.asyncio
async def test_close_is_idempotent_and_blocks_requeue(client, make_teacher, make_session, join):
    headers = await make_teacher("flow_close")
    code = await make_session(headers)
    await join(code, "Anna")
    eid = await entry_id(client, headers, code, "Anna")
    await post(client, headers, code, "skip", eid)

    assert (await post(client, headers, code, "close")).json()["status"] == "closed"
    assert (await post(client, headers, code, "close")).status_code == 200
    assert (await post(client, headers, code, "requeue", eid)).status_code == 409


@pytest.mark.asyncio
async def test_database_blocks_two_called_entries(
    db_session, client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_db_guard")
    code = await make_session(headers)
    await join(code, "Anna")
    await join(code, "Boris")
    await post(client, headers, code, "next")
    boris = await entry_id(client, headers, code, "Boris")

    with pytest.raises(IntegrityError):
        await db_session.execute(
            update(EntryORM).where(EntryORM.id == boris).values(status="called")
        )
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_sessions_are_listed_newest_first(client, make_teacher, make_session):
    headers = await make_teacher("flow_list")
    first = await make_session(headers, "First")
    second = await make_session(headers, "Second")
    listed = (await client.get(API, headers=headers)).json()
    assert [s["code"] for s in listed] == [second, first]


@pytest.mark.asyncio
async def test_health_and_pages(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/")).status_code == 200
    assert (await client.get("/teacher")).status_code == 200


@pytest.mark.asyncio
async def test_blank_title_rejected(client, make_teacher):
    headers = await make_teacher("flow_title")
    assert (await client.post(API, headers=headers, json={"title": "   "})).status_code == 422


@pytest.mark.asyncio
async def test_me_includes_now_serving_for_one_request_polling(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("flow_now_serving")
    code = await make_session(headers)
    await join(code, "Anna")
    boris = await join(code, "Boris")
    token = {"X-Entry-Token": boris["token"]}

    assert (await client.get(f"{API}/{code}/me", headers=token)).json()["now_serving"] is None
    await post(client, headers, code, "next")
    now = (await client.get(f"{API}/{code}/me", headers=token)).json()["now_serving"]
    assert now == {"ticket": 1, "name": None}


@pytest.mark.asyncio
async def test_call_next_race_is_closed_even_when_the_window_is_widened(
    client, make_teacher, make_session, join, monkeypatch
):
    headers = await make_teacher("flow_race_wide")
    code = await make_session(headers)
    for i in range(5):
        await join(code, f"s{i}")

    original = QueueRepository.get_called

    async def slow_get_called(self, session_id):
        result = await original(self, session_id)
        await asyncio.sleep(0.1)
        return result

    monkeypatch.setattr(QueueRepository, "get_called", slow_get_called)
    responses = await asyncio.gather(*(post(client, headers, code, "next") for _ in range(10)))
    assert sorted(r.status_code for r in responses) == [200] + [409] * 9
