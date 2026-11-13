import asyncio

import pytest
from sqlalchemy import func, select

from core.config import settings
from models.protocol import AuditLogORM
from services.assignment_copy import copy_title

API = "/api/v1"


def test_default_copy_title_gets_a_suffix_and_a_number_when_taken():
    assert copy_title("Работа", set()) == "Работа (копия)"
    assert copy_title("Работа", {"Работа (копия)"}) == "Работа (копия 2)"
    assert copy_title("Работа", {"Работа (копия)", "Работа (копия 2)"}) == "Работа (копия 3)"


def test_default_copy_title_never_exceeds_the_title_limit():
    long_title = "Д" * 120
    first = copy_title(long_title, set())
    second = copy_title(long_title, {first})

    assert len(first) <= 120 and first.endswith(" (копия)")
    assert len(second) <= 120 and second.endswith(" (копия 2)")
    assert first != second


@pytest.fixture
def source(client, make_teacher):
    async def _source(username: str, title: str = "Исходная работа", questions: int = 3):
        headers = await make_teacher(username)
        assignment = (
            await client.post(
                f"{API}/assignments",
                headers=headers,
                json={
                    "title": title,
                    "description": "Описание работы",
                    "questions_per_submission": 2,
                    "practice_enabled": True,
                },
            )
        ).json()
        for i in range(questions):
            r = await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": f"Вопрос {i}", "difficulty": 1 + i % 3, "topic": f"Тема {i}"},
            )
            assert r.status_code == 201, r.text
        for name, maximum in (("Код", 10), ("Защита", 5)):
            r = await client.post(
                f"{API}/assignments/{assignment['id']}/criteria",
                headers=headers,
                json={"name": name, "max_points": maximum},
            )
            assert r.status_code == 201, r.text
        return headers, assignment

    return _source


async def questions_of(client, headers, assignment_id):
    r = await client.get(f"{API}/assignments/{assignment_id}/questions", headers=headers)
    return r.json()


async def criteria_of(client, headers, assignment_id):
    r = await client.get(f"{API}/assignments/{assignment_id}/criteria", headers=headers)
    return r.json()


def shape(items, *keys):
    return [tuple(item[k] for k in keys) for item in items]


@pytest.mark.asyncio
async def test_copy_takes_criteria_questions_and_settings_with_a_default_title(client, source):
    headers, original = await source("copy_basic")

    r = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})

    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["id"] != original["id"]
    assert clone["title"] == "Исходная работа (копия)"
    assert clone["description"] == "Описание работы"
    assert clone["questions_per_submission"] == 2 and clone["practice_enabled"] is True
    assert clone["question_count"] == 3 and clone["criteria_count"] == 2
    assert clone["submissions_count"] == 0 and clone["criteria_locked"] is False
    assert clone["is_template"] is False
    assert shape(
        await questions_of(client, headers, clone["id"]), "text", "difficulty", "topic", "position"
    ) == shape(
        await questions_of(client, headers, original["id"]),
        "text",
        "difficulty",
        "topic",
        "position",
    )
    assert shape(await criteria_of(client, headers, clone["id"]), "name", "max_points") == [
        ("Код", 10),
        ("Защита", 5),
    ]
    original_ids = {q["id"] for q in await questions_of(client, headers, original["id"])}
    assert not original_ids & {q["id"] for q in await questions_of(client, headers, clone["id"])}


@pytest.mark.asyncio
async def test_copy_accepts_a_custom_title_and_numbers_default_titles(client, source):
    headers, original = await source("copy_titles")

    custom = await client.post(
        f"{API}/assignments/{original['id']}/copy", headers=headers, json={"title": "  Весна  "}
    )
    first = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
    second = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})

    assert custom.json()["title"] == "Весна"
    assert first.json()["title"] == "Исходная работа (копия)"
    assert second.json()["title"] == "Исходная работа (копия 2)"


@pytest.mark.asyncio
async def test_copy_with_a_taken_or_invalid_title_is_refused_in_russian(client, source):
    headers, original = await source("copy_bad_title")

    taken = await client.post(
        f"{API}/assignments/{original['id']}/copy",
        headers=headers,
        json={"title": original["title"]},
    )
    blank = await client.post(
        f"{API}/assignments/{original['id']}/copy", headers=headers, json={"title": "   "}
    )
    long = await client.post(
        f"{API}/assignments/{original['id']}/copy", headers=headers, json={"title": "я" * 121}
    )

    assert taken.status_code == 409
    assert taken.json()["code"] == "assignment_title_taken"
    assert taken.json()["detail"] == "Работа с таким названием уже есть"
    assert blank.status_code == 422 and long.status_code == 422
    assert len((await client.get(f"{API}/assignments", headers=headers)).json()) == 1


async def complete_one(client, headers, assignment, code, name="Студент"):
    await client.put(
        f"{API}/sessions/{code}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    await client.post(
        f"{API}/sessions/{code}/join", json={"name": name, "token": "tok-copy-" + "x" * 12}
    )
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    current = (
        await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
    ).json()
    criteria = await criteria_of(client, headers, assignment["id"])
    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": c["id"], "points": 1} for c in criteria]},
    )
    assert done.status_code == 200, done.text
    return current["id"]


@pytest.mark.asyncio
async def test_copy_does_not_take_submissions_scores_or_audit(
    client, source, make_session, db_session
):
    headers, original = await source("copy_clean")
    code = await make_session(headers)
    submission_id = await complete_one(client, headers, original, code)
    corrected = await client.post(
        f"{API}/submissions/{submission_id}/correct",
        headers=headers,
        json={"comment": "правка", "reason": "проверка"},
    )
    assert corrected.status_code == 200, corrected.text
    audit_before = (
        await db_session.execute(select(func.count()).select_from(AuditLogORM))
    ).scalar_one()
    assert audit_before > 0

    r = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})

    clone = r.json()
    assert clone["submissions_count"] == 0
    assert clone["criteria_locked"] is False
    assert (await client.get(f"{API}/assignments/{original['id']}", headers=headers)).json()[
        "submissions_count"
    ] == 1
    audit_after = (
        await db_session.execute(select(func.count()).select_from(AuditLogORM))
    ).scalar_one()
    assert audit_after == audit_before
    added = await client.post(
        f"{API}/assignments/{clone['id']}/criteria",
        headers=headers,
        json={"name": "Новый критерий", "max_points": 3},
    )
    assert added.status_code == 201


@pytest.mark.asyncio
async def test_copies_are_independent_of_the_source_and_of_each_other(client, source):
    headers, original = await source("copy_independent")
    clone = (
        await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
    ).json()
    clone_questions = await questions_of(client, headers, clone["id"])
    source_questions = await questions_of(client, headers, original["id"])

    await client.patch(
        f"{API}/assignments/{clone['id']}/questions/{clone_questions[0]['id']}",
        headers=headers,
        json={"text": "Изменённый вопрос"},
    )
    await client.delete(
        f"{API}/assignments/{original['id']}/questions/{source_questions[1]['id']}", headers=headers
    )
    await client.post(
        f"{API}/assignments/{clone['id']}/criteria",
        headers=headers,
        json={"name": "Только в копии", "max_points": 2},
    )

    source_after = await questions_of(client, headers, original["id"])
    clone_after = await questions_of(client, headers, clone["id"])
    assert [q["text"] for q in source_after] == ["Вопрос 0", "Вопрос 2"]
    assert [q["text"] for q in clone_after] == ["Изменённый вопрос", "Вопрос 1", "Вопрос 2"]
    assert len(await criteria_of(client, headers, original["id"])) == 2
    assert len(await criteria_of(client, headers, clone["id"])) == 3

    removed = await client.delete(f"{API}/assignments/{original['id']}", headers=headers)
    assert removed.status_code == 204
    survivor = await client.get(f"{API}/assignments/{clone['id']}", headers=headers)
    assert survivor.status_code == 200 and survivor.json()["question_count"] == 3


@pytest.mark.asyncio
async def test_another_teachers_assignment_cannot_be_copied(client, source, make_teacher):
    headers, original = await source("copy_owner")
    stranger = await make_teacher("copy_stranger")

    r = await client.post(f"{API}/assignments/{original['id']}/copy", headers=stranger, json={})
    anonymous = await client.post(f"{API}/assignments/{original['id']}/copy", json={})
    missing = await client.post(f"{API}/assignments/no-such-id/copy", headers=headers, json={})

    assert r.status_code == 404 and r.json()["code"] == "assignment_not_found"
    assert anonymous.status_code == 401
    assert missing.status_code == 404
    assert (await client.get(f"{API}/assignments", headers=stranger)).json() == []


@pytest.mark.asyncio
async def test_template_flag_lists_only_own_templates_and_creates_independent_works(
    client, source, make_teacher
):
    headers, original = await source("tpl_owner")
    stranger = await make_teacher("tpl_stranger")
    await client.post(
        f"{API}/assignments", headers=stranger, json={"title": "Чужой шаблон", "is_template": True}
    )

    assert (await client.get(f"{API}/assignment-templates", headers=headers)).json() == []
    flagged = await client.patch(
        f"{API}/assignments/{original['id']}", headers=headers, json={"is_template": True}
    )
    assert flagged.status_code == 200 and flagged.json()["is_template"] is True

    listed = (await client.get(f"{API}/assignment-templates", headers=headers)).json()
    assert [t["id"] for t in listed] == [original["id"]]
    created = await client.post(
        f"{API}/assignment-templates/{original['id']}/create",
        headers=headers,
        json={"title": "Из шаблона"},
    )
    assert created.status_code == 201, created.text
    made = created.json()
    assert made["title"] == "Из шаблона" and made["is_template"] is False
    assert made["question_count"] == 3 and made["criteria_count"] == 2
    assert (await client.get(f"{API}/assignment-templates", headers=headers)).json()[0][
        "id"
    ] == original["id"]
    default = await client.post(
        f"{API}/assignment-templates/{original['id']}/create", headers=headers, json={}
    )
    assert default.json()["title"] == "Исходная работа (копия)"


@pytest.mark.asyncio
async def test_a_work_that_is_not_a_template_or_not_yours_cannot_be_created_from_template(
    client, source, make_teacher
):
    headers, original = await source("tpl_guard")
    stranger = await make_teacher("tpl_guard_other")

    plain = await client.post(
        f"{API}/assignment-templates/{original['id']}/create", headers=headers, json={}
    )
    await client.patch(
        f"{API}/assignments/{original['id']}", headers=headers, json={"is_template": True}
    )
    foreign = await client.post(
        f"{API}/assignment-templates/{original['id']}/create", headers=stranger, json={}
    )
    anonymous = await client.get(f"{API}/assignment-templates")

    assert plain.status_code == 404 and plain.json()["code"] == "assignment_not_found"
    assert foreign.status_code == 404
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_copy_respects_the_assignment_limit(client, source, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ASSIGNMENTS_PER_TEACHER", 2)
    headers, original = await source("copy_limit")

    first = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
    second = await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})

    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["code"] == "assignment_limit"
    assert second.json()["detail"] == "Достигнут предел числа работ"
    assert len((await client.get(f"{API}/assignments", headers=headers)).json()) == 2


@pytest.mark.asyncio
async def test_copy_of_a_maximal_bank_keeps_every_item(client, source, monkeypatch):
    monkeypatch.setattr(settings, "MAX_QUESTIONS_PER_ASSIGNMENT", 12)
    monkeypatch.setattr(settings, "MAX_CRITERIA_PER_ASSIGNMENT", 3)
    headers, original = await source("copy_max", questions=12)
    await client.post(
        f"{API}/assignments/{original['id']}/criteria",
        headers=headers,
        json={"name": "Третий", "max_points": 1},
    )

    clone = (
        await client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
    ).json()

    assert clone["question_count"] == 12 and clone["criteria_count"] == 3
    over = await client.post(
        f"{API}/assignments/{clone['id']}/questions", headers=headers, json={"text": "Лишний"}
    )
    assert over.status_code == 409 and over.json()["code"] == "question_limit"


@pytest.mark.asyncio
async def test_parallel_copies_get_distinct_titles(client, source):
    headers, original = await source("copy_race_titles")

    results = await asyncio.gather(
        *[
            client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
            for _ in range(8)
        ]
    )

    assert [r.status_code for r in results] == [201] * 8
    titles = [r.json()["title"] for r in results]
    assert len(set(titles)) == 8
    assert len((await client.get(f"{API}/assignments", headers=headers)).json()) == 9


@pytest.mark.asyncio
async def test_parallel_copies_cannot_exceed_the_assignment_limit(client, source, monkeypatch):
    monkeypatch.setattr(settings, "MAX_ASSIGNMENTS_PER_TEACHER", 4)
    headers, original = await source("copy_race_limit")

    results = await asyncio.gather(
        *[
            client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={})
            for _ in range(8)
        ]
    )

    codes = sorted(r.status_code for r in results)
    assert codes == [201, 201, 201] + [409] * 5
    assert len((await client.get(f"{API}/assignments", headers=headers)).json()) == 4


@pytest.mark.asyncio
async def test_copy_racing_a_question_edit_sees_a_consistent_bank(client, source):
    headers, original = await source("copy_race_edit", questions=5)
    questions = await questions_of(client, headers, original["id"])

    results = await asyncio.gather(
        client.post(f"{API}/assignments/{original['id']}/copy", headers=headers, json={}),
        *[
            client.delete(
                f"{API}/assignments/{original['id']}/questions/{q['id']}", headers=headers
            )
            for q in questions[:2]
        ],
    )

    clone = results[0].json()
    assert results[0].status_code == 201
    assert clone["question_count"] in {3, 4, 5}
    texts = [q["text"] for q in await questions_of(client, headers, clone["id"])]
    assert len(texts) == len(set(texts)) == clone["question_count"]
