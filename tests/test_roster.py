import asyncio
import uuid

import pytest

from core.config import settings
from services.roster import clean_name, parse_names

API = "/api/v1"
GROUP = "ИС-21"
LIST = "1. Иванов Иван\n2) Петрова Анна\n- Сидоров Пётр\n\n  Иванов   Иван \nКузнецова Мария"


@pytest.fixture
def teacher(make_teacher):
    async def _teacher(prefix: str):
        return await make_teacher(f"{prefix}_{uuid.uuid4().hex[:6]}")

    return _teacher


async def load(client, headers, group=GROUP, text=LIST):
    return await client.put(f"{API}/roster", headers=headers, json={"group": group, "text": text})


async def open_session(client, headers, group=GROUP, **extra):
    response = await client.post(
        f"{API}/sessions",
        headers=headers,
        json={"title": "Зачёт", "group": group, "session_mode": "queue_only", **extra},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_numbering_bullets_and_spaces_are_removed_but_initials_stay():
    assert clean_name("1. Иванов Иван") == "Иванов Иван"
    assert clean_name("  (2)  Петрова   Анна ") == "Петрова Анна"
    assert clean_name("- Сидоров Пётр") == "Сидоров Пётр"
    assert clean_name("А. С. Пушкин") == "А. С. Пушкин"
    assert clean_name("2024 Иванов") == "2024 Иванов"


def test_parsing_reports_duplicates_long_lines_and_the_limit():
    names, skipped = parse_names("Анна\nанна\n" + "я" * 61 + "\nБорис\nВера", limit=2)

    assert names == ["Анна", "Борис"]
    assert {(s.reason) for s in skipped} == {"duplicate", "too_long", "over_limit"}


@pytest.mark.asyncio
async def test_a_teacher_uploads_a_list_and_gets_it_back_sorted_without_repeats(client, teacher):
    headers = await teacher("ro_up")

    saved = await load(client, headers)
    again = await client.get(f"{API}/roster/names", headers=headers, params={"group": GROUP})
    groups = await client.get(f"{API}/roster", headers=headers)

    assert saved.status_code == 200, saved.text
    assert saved.json()["names"] == [
        "Иванов Иван",
        "Кузнецова Мария",
        "Петрова Анна",
        "Сидоров Пётр",
    ]
    assert [s["reason"] for s in saved.json()["skipped"]] == ["duplicate"]
    assert again.json()["names"] == saved.json()["names"]
    assert groups.json() == [{"group": GROUP, "count": 4}]


@pytest.mark.asyncio
async def test_uploading_again_replaces_the_list_and_group_spelling_is_ignored(client, teacher):
    headers = await teacher("ro_replace")
    await load(client, headers)

    await load(client, headers, group="ис - 21", text="Новый Студент")
    listed = (
        await client.get(f"{API}/roster/names", headers=headers, params={"group": GROUP})
    ).json()
    groups = (await client.get(f"{API}/roster", headers=headers)).json()

    assert listed["names"] == ["Новый Студент"]
    assert len(groups) == 1 and groups[0]["count"] == 1


@pytest.mark.asyncio
async def test_an_empty_list_and_a_missing_list_are_reported(client, teacher):
    headers = await teacher("ro_empty")

    empty = await load(client, headers, text="\n  \n")
    missing = await client.get(f"{API}/roster/names", headers=headers, params={"group": "Нет"})
    gone = await client.delete(f"{API}/roster", headers=headers, params={"group": "Нет"})

    assert empty.status_code == 422 and empty.json()["code"] == "roster_empty"
    assert missing.status_code == 404 and missing.json()["code"] == "roster_not_found"
    assert gone.status_code == 404


@pytest.mark.asyncio
async def test_a_list_can_be_deleted(client, teacher):
    headers = await teacher("ro_del")
    await load(client, headers)

    deleted = await client.delete(f"{API}/roster", headers=headers, params={"group": GROUP})
    after = await client.get(f"{API}/roster", headers=headers)

    assert deleted.status_code == 204 and after.json() == []


@pytest.mark.asyncio
async def test_lists_are_private_to_their_teacher_and_need_a_login(client, teacher):
    owner = await teacher("ro_owner")
    other = await teacher("ro_other")
    await load(client, owner)

    seen = await client.get(f"{API}/roster/names", headers=other, params={"group": GROUP})
    listed = await client.get(f"{API}/roster", headers=other)
    anonymous = await client.get(f"{API}/roster")

    assert seen.status_code == 404
    assert listed.json() == []
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_the_size_limits_are_enforced(client, teacher, monkeypatch):
    headers = await teacher("ro_limits")
    monkeypatch.setattr(settings, "MAX_ROSTER_PER_GROUP", 2)
    monkeypatch.setattr(settings, "MAX_ROSTER_GROUPS", 1)

    first = await load(client, headers, text="А Б\nВ Г\nД Е")
    second = await load(client, headers, group="Другая", text="Ж З")
    same = await load(client, headers, text="И К")

    assert [s["reason"] for s in first.json()["skipped"]] == ["over_limit"]
    assert second.status_code == 409 and second.json()["code"] == "roster_groups_limit"
    assert same.status_code == 200


@pytest.mark.asyncio
async def test_the_student_page_gets_only_names_not_yet_in_the_queue(client, teacher, join):
    headers = await teacher("ro_public")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)
    await join(session["code"], "Иванов Иван")

    roster = (await client.get(f"{API}/sessions/{session['code']}/roster")).json()
    info = (await client.get(f"{API}/sessions/{session['code']}")).json()

    assert roster["only"] is True
    assert "Иванов Иван" not in roster["names"] and "Петрова Анна" in roster["names"]
    assert info["roster_only"] is True and info["roster_available"] is True


@pytest.mark.asyncio
async def test_a_session_without_a_list_shows_nothing_and_is_not_restricted(client, teacher):
    headers = await teacher("ro_none")
    session = await open_session(client, headers)

    roster = (await client.get(f"{API}/sessions/{session['code']}/roster")).json()
    info = (await client.get(f"{API}/sessions/{session['code']}")).json()

    assert roster == {"names": [], "only": False}
    assert info["roster_available"] is False and info["roster_only"] is False


@pytest.mark.asyncio
async def test_roster_only_sessions_need_a_list_for_their_group(client, teacher):
    headers = await teacher("ro_need")

    created = await client.post(
        f"{API}/sessions",
        headers=headers,
        json={"title": "Зачёт", "group": "Без списка", "roster_only": True},
    )
    session = await open_session(client, headers)
    switched = await client.put(
        f"{API}/sessions/{session['code']}/settings", headers=headers, json={"roster_only": True}
    )

    assert created.status_code == 422 and created.json()["code"] == "roster_missing"
    assert switched.status_code == 422 and switched.json()["code"] == "roster_missing"


@pytest.mark.asyncio
async def test_joining_by_list_uses_the_spelling_from_the_list(client, teacher, join):
    headers = await teacher("ro_join")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)

    entry = await join(session["code"], "  иванов   ИВАН ")

    assert entry["name"] == "Иванов Иван"


@pytest.mark.asyncio
async def test_a_name_outside_the_list_is_refused(client, teacher):
    headers = await teacher("ro_out")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)

    response = await client.post(
        f"{API}/sessions/{session['code']}/join",
        json={"name": "Посторонний Человек", "token": "x" * 20},
    )

    assert response.status_code == 422 and response.json()["code"] == "not_in_roster"


@pytest.mark.asyncio
async def test_the_same_name_cannot_be_used_from_two_devices_but_a_refresh_works(
    client, teacher, join
):
    headers = await teacher("ro_twice")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)
    first = await join(session["code"], "Петрова Анна", token="a" * 20)

    refresh = await join(session["code"], "Петрова Анна", token="a" * 20)
    second = await client.post(
        f"{API}/sessions/{session['code']}/join",
        json={"name": "Петрова Анна", "token": "b" * 20},
    )

    assert refresh["ticket"] == first["ticket"]
    assert second.status_code == 409 and second.json()["code"] == "name_taken"


@pytest.mark.asyncio
async def test_a_student_who_left_can_join_again_under_the_same_name(client, teacher, join):
    headers = await teacher("ro_left")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)
    await join(session["code"], "Петрова Анна", token="a" * 20)
    await client.post(
        f"{API}/sessions/{session['code']}/leave", headers={"X-Entry-Token": "a" * 20}
    )

    again = await join(session["code"], "Петрова Анна", token="c" * 20)

    assert again["status"] == "waiting"


@pytest.mark.asyncio
async def test_without_the_restriction_any_name_is_accepted_as_before(client, teacher, join):
    headers = await teacher("ro_free")
    await load(client, headers)
    session = await open_session(client, headers)

    entry = await join(session["code"], "Кто Угодно")

    assert entry["name"] == "Кто Угодно"


@pytest.mark.asyncio
async def test_the_history_follows_the_roster_name_between_sessions(client, teacher, join):
    headers = await teacher("ro_history")
    await load(client, headers)
    first = await open_session(client, headers, roster_only=True)
    second = await open_session(client, headers, roster_only=True)

    await join(first["code"], "Иванов Иван", token="a" * 20)
    await join(second["code"], "ИВАНОВ иван", token="b" * 20)
    students = (await client.get(f"{API}/students", headers=headers)).json()

    assert len([s for s in students if s["name"] == "Иванов Иван"]) == 1


@pytest.mark.asyncio
async def test_concurrent_joins_under_one_name_create_one_entry(client, teacher):
    headers = await teacher("ro_race")
    await load(client, headers)
    session = await open_session(client, headers, roster_only=True)

    async def attempt(i: int):
        return await client.post(
            f"{API}/sessions/{session['code']}/join",
            json={"name": "Кузнецова Мария", "token": f"{i}".ljust(20, "t")},
        )

    results = await asyncio.gather(*(attempt(i) for i in range(6)))
    queue = (await client.get(f"{API}/sessions/{session['code']}/queue", headers=headers)).json()

    assert sorted(r.status_code for r in results) == [200, 409, 409, 409, 409, 409]
    assert [e["name"] for e in queue["entries"]] == ["Кузнецова Мария"]


@pytest.mark.asyncio
async def test_two_uploads_at_once_leave_one_consistent_list(client, teacher):
    headers = await teacher("ro_parallel")

    results = await asyncio.gather(
        load(client, headers, text="Анна\nБорис"),
        load(client, headers, text="Вера\nГлеб\nДарья"),
        load(client, headers, text="Егор"),
    )
    final = (
        await client.get(f"{API}/roster/names", headers=headers, params={"group": GROUP})
    ).json()

    assert all(r.status_code == 200 for r in results)
    assert final["names"] in (["Анна", "Борис"], ["Вера", "Глеб", "Дарья"], ["Егор"])
