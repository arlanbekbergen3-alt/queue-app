import asyncio

import pytest

API = "/api/v1/sessions"


async def create(client, headers, title="Lab", group=None):
    payload = {"title": title}
    if group is not None:
        payload["group"] = group
    return await client.post(API, headers=headers, json=payload)


async def queue(client, headers, code):
    return (await client.get(f"{API}/{code}/queue", headers=headers)).json()


async def act(client, headers, code, action, entry=None):
    suffix = f"entries/{entry}/{action}" if entry else action
    return await client.post(f"{API}/{code}/{suffix}", headers=headers)


@pytest.mark.asyncio
async def test_session_group_is_optional_trimmed_and_returned_everywhere(client, make_teacher):
    headers = await make_teacher("multi_group")

    plain = (await create(client, headers, "Без группы")).json()
    grouped = (await create(client, headers, "С группой", "  ИС-22  ")).json()

    assert plain["group"] is None
    assert grouped["group"] == "ИС-22"
    listed = {s["code"]: s["group"] for s in (await client.get(API, headers=headers)).json()}
    assert listed == {plain["code"]: None, grouped["code"]: "ИС-22"}
    public = (await client.get(f"{API}/{grouped['code']}")).json()
    assert (public["title"], public["group"]) == ("С группой", "ИС-22")
    assert (await queue(client, headers, grouped["code"]))["group"] == "ИС-22"


@pytest.mark.asyncio
async def test_blank_group_becomes_none_and_too_long_group_is_rejected(client, make_teacher):
    headers = await make_teacher("multi_group_bad")

    assert (await create(client, headers, "A", "   ")).json()["group"] is None
    assert (await create(client, headers, "B", "x" * 61)).status_code == 422
    assert (await create(client, headers, "C", "a\x00b")).status_code == 422


@pytest.mark.asyncio
async def test_one_teacher_runs_two_sessions_with_independent_queues(client, make_teacher, join):
    headers = await make_teacher("multi_two_sessions")
    first = (await create(client, headers, "ИС-21", "ИС-21")).json()["code"]
    second = (await create(client, headers, "ИС-22", "ИС-22")).json()["code"]

    shared_token = "s" * 20
    a1 = await join(first, "Anna", shared_token)
    a2 = await join(second, "Anna", shared_token)
    b2 = await join(second, "Boris")

    assert (a1["ticket"], a2["ticket"], b2["ticket"]) == (1, 1, 2)
    called = (await act(client, headers, second, "next")).json()
    assert called["name"] == "Anna"
    assert (await queue(client, headers, first))["entries"][0]["status"] == "waiting"
    assert (await queue(client, headers, second))["entries"][0]["status"] == "called"
    await act(client, headers, second, "close")
    assert (await queue(client, headers, first))["status"] == "open"


@pytest.mark.asyncio
async def test_teachers_see_only_their_own_sessions_and_queues(client, make_teacher, join):
    alice = await make_teacher("multi_alice")
    bob = await make_teacher("multi_bob")
    alice_code = (await create(client, alice, "Alice lab", "A-1")).json()["code"]
    bob_code = (await create(client, bob, "Bob lab", "B-1")).json()["code"]
    await join(alice_code, "Student")

    assert [s["title"] for s in (await client.get(API, headers=alice)).json()] == ["Alice lab"]
    assert [s["title"] for s in (await client.get(API, headers=bob)).json()] == ["Bob lab"]
    assert (await client.get(f"{API}/{alice_code}/queue", headers=bob)).status_code == 404
    assert (await queue(client, bob, bob_code))["entries"] == []


@pytest.mark.asyncio
async def test_teacher_cannot_change_another_teachers_queue_by_any_action(
    client, make_teacher, join
):
    alice = await make_teacher("multi_a2")
    bob = await make_teacher("multi_b2")
    code = (await create(client, alice, "Alice")).json()["code"]
    await join(code, "S1")
    await join(code, "S2")
    entries = (await queue(client, alice, code))["entries"]
    eid = entries[0]["id"]

    attempts = [
        await act(client, bob, code, "next"),
        await act(client, bob, code, "close"),
        *[await act(client, bob, code, a, eid) for a in ("done", "skip", "requeue", "promote")],
    ]

    assert {r.status_code for r in attempts} == {404}
    after = await queue(client, alice, code)
    assert [e["status"] for e in after["entries"]] == ["waiting", "waiting"]
    assert after["status"] == "open"


@pytest.mark.asyncio
async def test_two_teachers_call_next_at_the_same_time_without_interference(
    client, make_teacher, join
):
    alice = await make_teacher("multi_par_a")
    bob = await make_teacher("multi_par_b")
    codes = {}
    for who, headers in (("alice", alice), ("bob", bob)):
        codes[who] = (await create(client, headers, who)).json()["code"]
        for i in range(3):
            await join(codes[who], f"{who}{i}")

    results = await asyncio.gather(
        *(act(client, h, codes[w], "next") for w, h in (("alice", alice), ("bob", bob)) * 3)
    )

    assert sorted(r.status_code for r in results) == [200, 200, 409, 409, 409, 409]
    assert (await queue(client, alice, codes["alice"]))["entries"][0]["name"] == "alice0"
    assert (await queue(client, bob, codes["bob"]))["entries"][0]["name"] == "bob0"


@pytest.mark.asyncio
async def test_student_token_from_one_session_is_useless_in_another(client, make_teacher, join):
    headers = await make_teacher("multi_scope")
    first = (await create(client, headers, "One")).json()["code"]
    second = (await create(client, headers, "Two")).json()["code"]
    student = await join(first, "Anna")

    response = await client.get(f"{API}/{second}/me", headers={"X-Entry-Token": student["token"]})

    assert response.status_code == 404
