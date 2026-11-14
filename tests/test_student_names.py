import json

import pytest
import pytest_asyncio

from services.live import hub, student_stream
from tests.test_live import event

API = "/api/v1"
S = f"{API}/sessions"
SECRET = "Тайный Студент"


@pytest_asyncio.fixture(autouse=True)
async def clean_hub():
    yield
    await hub.close()
    hub._states.clear()


def tok(student):
    return {"X-Entry-Token": student["token"]}


@pytest.fixture
def room(client, make_teacher, join):
    async def _room(username: str, **settings):
        headers = await make_teacher(username)
        created = await client.post(S, headers=headers, json={"title": "Сдача", **settings})
        assert created.status_code == 201, created.text
        code = created.json()["code"]
        called = await join(code, SECRET, "token-called-0001")
        waiting = await join(code, "Ожидающий", "token-waiting-002")
        assert (await client.post(f"{S}/{code}/next", headers=headers)).status_code == 200
        return headers, code, called, waiting

    return _room


@pytest.mark.asyncio
async def test_names_of_other_students_are_hidden_by_default(client, room):
    headers, code, called, waiting = await room("names_default")

    mine = await client.get(f"{S}/{code}/me", headers=tok(waiting))
    public = await client.get(f"{S}/{code}")

    for response in (mine, public):
        assert response.json()["now_serving"]["ticket"] == called["ticket"]
        assert response.json()["now_serving"]["name"] is None
        assert SECRET not in response.text


@pytest.mark.asyncio
async def test_a_student_still_sees_their_own_name(client, room):
    _, code, called, _ = await room("names_own")

    mine = await client.get(f"{S}/{code}/me", headers=tok(called))

    assert mine.json()["name"] == SECRET


@pytest.mark.asyncio
async def test_the_live_stream_does_not_carry_the_name_either(client, room):
    headers, code, _, waiting = await room("names_stream")
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    entry_id = next(e["id"] for e in queue["entries"] if e["name"] == "Ожидающий")

    stream = student_stream(code, entry_id)
    _, data = await event(stream)
    await stream.aclose()

    assert data["now_serving"]["name"] is None
    assert SECRET not in json.dumps(data, ensure_ascii=False)


@pytest.mark.asyncio
async def test_the_name_is_shown_when_the_teacher_turns_the_setting_on(client, room):
    headers, code, _, waiting = await room("names_on", show_student_names=True)

    mine = await client.get(f"{S}/{code}/me", headers=tok(waiting))
    public = await client.get(f"{S}/{code}")

    assert mine.json()["now_serving"]["name"] == SECRET
    assert public.json()["now_serving"]["name"] == SECRET


@pytest.mark.asyncio
async def test_the_setting_can_be_switched_after_registration_is_closed(client, room):
    headers, code, _, waiting = await room("names_switch")
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": False})

    on = await client.put(
        f"{S}/{code}/settings", headers=headers, json={"show_student_names": True}
    )
    shown = await client.get(f"{S}/{code}/me", headers=tok(waiting))
    off = await client.put(
        f"{S}/{code}/settings", headers=headers, json={"show_student_names": False}
    )
    hidden = await client.get(f"{S}/{code}/me", headers=tok(waiting))

    assert on.status_code == 200 and on.json()["show_student_names"] is True
    assert shown.json()["now_serving"]["name"] == SECRET
    assert off.json()["show_student_names"] is False
    assert hidden.json()["now_serving"]["name"] is None


@pytest.mark.asyncio
async def test_other_settings_stay_locked_after_registration_is_closed(client, room):
    headers, code, _, _ = await room("names_locked")
    await client.put(f"{S}/{code}/registration", headers=headers, json={"open": False})

    locked = await client.put(f"{S}/{code}/settings", headers=headers, json={"order_mode": "join"})

    assert locked.status_code == 409 and locked.json()["code"] == "settings_locked"


@pytest.mark.asyncio
async def test_only_the_owner_can_change_the_setting(client, room, make_teacher):
    _, code, _, _ = await room("names_owner")
    stranger = await make_teacher("names_stranger")

    response = await client.put(
        f"{S}/{code}/settings", headers=stranger, json={"show_student_names": True}
    )

    assert response.status_code in (403, 404)


@pytest.mark.asyncio
async def test_the_teacher_still_sees_every_name(client, room):
    headers, code, _, _ = await room("names_teacher")

    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()

    assert {e["name"] for e in queue["entries"]} == {SECRET, "Ожидающий"}
