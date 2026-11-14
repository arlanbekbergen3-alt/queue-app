import uuid

import pytest

API = "/api/v1"
RUBRIC = [
    {"points": 3, "text": "Верно и полно"},
    {"points": 0, "text": "Нет ответа"},
    {"points": 2, "text": "Верно с неточностями"},
    {"points": 1, "text": "Частично"},
]


@pytest.fixture
def work(client, make_teacher):
    async def _work(username: str):
        headers = await make_teacher(f"{username}_{uuid.uuid4().hex[:6]}")
        made = await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {username}", "questions_per_submission": 1},
        )
        assert made.status_code == 201, made.text
        return headers, made.json()

    return _work


async def add_criterion(client, headers, assignment, **body):
    body.setdefault("name", "Ответ")
    body.setdefault("max_points", 3)
    return await client.post(
        f"{API}/assignments/{assignment['id']}/criteria", headers=headers, json=body
    )


@pytest.mark.asyncio
async def test_a_criterion_without_levels_still_works_and_reports_an_empty_list(client, work):
    headers, assignment = await work("lv_plain")

    created = (await add_criterion(client, headers, assignment)).json()
    listed = (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=headers)
    ).json()

    assert created["levels"] == []
    assert listed[0]["levels"] == []


@pytest.mark.asyncio
async def test_levels_are_stored_sorted_by_points_and_returned(client, work):
    headers, assignment = await work("lv_sorted")

    created = await add_criterion(client, headers, assignment, levels=RUBRIC)
    listed = (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=headers)
    ).json()

    assert created.status_code == 201, created.text
    assert [level["points"] for level in listed[0]["levels"]] == [0, 1, 2, 3]
    assert listed[0]["levels"][0] == {"points": 0, "text": "Нет ответа"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("levels", "status", "code"),
    [
        ([{"points": 4, "text": "Слишком много"}], 422, "levels_out_of_range"),
        ([{"points": 1, "text": "а"}, {"points": 1, "text": "б"}], 422, None),
        ([{"points": -1, "text": "минус"}], 422, None),
        ([{"points": 1, "text": "   "}], 422, None),
        ([{"points": 1, "text": "я" * 201}], 422, None),
    ],
)
async def test_invalid_levels_are_rejected(client, work, levels, status, code):
    headers, assignment = await work("lv_bad")

    response = await add_criterion(client, headers, assignment, levels=levels)

    assert response.status_code == status, response.text
    if code:
        assert response.json()["code"] == code


@pytest.mark.asyncio
async def test_at_most_eleven_levels_are_accepted(client, work):
    headers, assignment = await work("lv_many")
    eleven = [{"points": i, "text": f"Уровень {i}"} for i in range(11)]
    twelve = [{"points": i, "text": f"Уровень {i}"} for i in range(12)]

    ok = await add_criterion(
        client, headers, assignment, name="Десять", max_points=10, levels=eleven
    )
    too_many = await add_criterion(
        client, headers, assignment, name="Одиннадцать", max_points=11, levels=twelve
    )

    assert ok.status_code == 201, ok.text
    assert too_many.status_code == 422


@pytest.mark.asyncio
async def test_levels_can_be_replaced_and_cleared(client, work):
    headers, assignment = await work("lv_put")
    criterion = (await add_criterion(client, headers, assignment)).json()
    url = f"{API}/assignments/{assignment['id']}/criteria/{criterion['id']}/levels"

    put = await client.put(url, headers=headers, json={"levels": RUBRIC})
    cleared = await client.put(url, headers=headers, json={"levels": []})

    assert put.status_code == 200 and len(put.json()["levels"]) == 4
    assert cleared.status_code == 200 and cleared.json()["levels"] == []


@pytest.mark.asyncio
async def test_levels_above_the_maximum_are_refused_when_replaced(client, work):
    headers, assignment = await work("lv_put_bad")
    criterion = (await add_criterion(client, headers, assignment)).json()
    url = f"{API}/assignments/{assignment['id']}/criteria/{criterion['id']}/levels"

    response = await client.put(
        url, headers=headers, json={"levels": [{"points": 9, "text": "Слишком много"}]}
    )

    assert response.status_code == 422 and response.json()["code"] == "levels_out_of_range"


@pytest.mark.asyncio
async def test_lowering_the_maximum_below_a_level_is_refused(client, work):
    headers, assignment = await work("lv_lower")
    criterion = (await add_criterion(client, headers, assignment, levels=RUBRIC)).json()

    response = await client.patch(
        f"{API}/assignments/{assignment['id']}/criteria/{criterion['id']}",
        headers=headers,
        json={"max_points": 2},
    )
    unchanged = (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=headers)
    ).json()

    assert response.status_code == 422 and response.json()["code"] == "levels_out_of_range"
    assert unchanged[0]["max_points"] == 3 and len(unchanged[0]["levels"]) == 4


@pytest.mark.asyncio
async def test_levels_can_be_edited_after_the_first_submission_but_points_cannot(
    client, make_session, join, work
):
    headers, assignment = await work("lv_locked")
    criterion = (await add_criterion(client, headers, assignment, levels=RUBRIC)).json()
    await client.post(
        f"{API}/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
    )
    code = await make_session(headers)
    await client.put(
        f"{API}/sessions/{code}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    await join(code, "Студент")
    assert (await client.post(f"{API}/sessions/{code}/next", headers=headers)).status_code == 200
    base = f"{API}/assignments/{assignment['id']}/criteria/{criterion['id']}"

    levels = await client.put(
        f"{base}/levels", headers=headers, json={"levels": [{"points": 3, "text": "Отлично"}]}
    )
    points = await client.patch(base, headers=headers, json={"max_points": 5})

    assert levels.status_code == 200 and levels.json()["levels"][0]["text"] == "Отлично"
    assert points.status_code == 409 and points.json()["code"] == "criteria_locked"


@pytest.mark.asyncio
async def test_another_teacher_cannot_touch_the_levels(client, make_teacher, work):
    headers, assignment = await work("lv_owner")
    criterion = (await add_criterion(client, headers, assignment)).json()
    stranger = await make_teacher("lv_stranger")

    response = await client.put(
        f"{API}/assignments/{assignment['id']}/criteria/{criterion['id']}/levels",
        headers=stranger,
        json={"levels": RUBRIC},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_copy_of_the_assignment_keeps_the_levels(client, work):
    headers, assignment = await work("lv_copy")
    await add_criterion(client, headers, assignment, levels=RUBRIC)

    copy = (
        await client.post(f"{API}/assignments/{assignment['id']}/copy", headers=headers, json={})
    ).json()
    criteria = (
        await client.get(f"{API}/assignments/{copy['id']}/criteria", headers=headers)
    ).json()

    assert [level["points"] for level in criteria[0]["levels"]] == [0, 1, 2, 3]


@pytest.mark.asyncio
async def test_levels_travel_through_a_published_snapshot_and_its_copy(client, make_teacher, work):
    headers, assignment = await work("lv_space_owner")
    await add_criterion(client, headers, assignment, levels=RUBRIC)
    await client.post(
        f"{API}/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
    )
    space = (await client.post(f"{API}/spaces", headers=headers, json={"name": "Предмет"})).json()
    guest = await make_teacher("lv_space_guest")
    await client.post(f"{API}/spaces/join", headers=guest, json={"code": space["invite_code"]})
    published = (
        await client.post(
            f"{API}/spaces/{space['id']}/publications",
            headers=headers,
            json={"assignment_id": assignment["id"]},
        )
    ).json()

    seen = (
        await client.get(
            f"{API}/spaces/{space['id']}/publications/{published['id']}", headers=guest
        )
    ).json()
    copied = (
        await client.post(
            f"{API}/spaces/{space['id']}/publications/{published['id']}/copy",
            headers=guest,
            json={},
        )
    ).json()
    criteria = (
        await client.get(f"{API}/assignments/{copied['id']}/criteria", headers=guest)
    ).json()

    assert [level["text"] for level in seen["criteria"][0]["levels"]][0] == "Нет ответа"
    assert [level["points"] for level in criteria[0]["levels"]] == [0, 1, 2, 3]
