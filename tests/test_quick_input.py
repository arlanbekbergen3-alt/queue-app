import asyncio
from uuid import uuid4

import pytest
import pytest_asyncio

from core.config import settings
from services.criteria_presets import PRESET_KEYS, PRESETS
from services.question_import import clean_line, parse

API = "/api/v1"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("1. Что такое индекс?", "Что такое индекс?"),
        ("2) Что такое индекс?", "Что такое индекс?"),
        ("(3) Что такое индекс?", "Что такое индекс?"),
        ("4: Что такое индекс?", "Что такое индекс?"),
        ("12 - Что такое индекс?", "Что такое индекс?"),
        ("- Что такое индекс?", "Что такое индекс?"),
        ("• Что такое индекс?", "Что такое индекс?"),
        ("* Что такое индекс?", "Что такое индекс?"),
        ("— Что такое индекс?", "Что такое индекс?"),
        ("а) Что такое индекс?", "Что такое индекс?"),
        ("1.1) Что такое индекс?", "1.1) Что такое индекс?"),
        ("  1.   - Что   такое   индекс?  ", "Что такое индекс?"),
        ("2024 год основания SQL?", "2024 год основания SQL?"),
        ("3 примера индексов", "3 примера индексов"),
        ("", ""),
        ("   ", ""),
        ("1.", ""),
    ],
)
def test_numbering_and_markers_are_removed_without_eating_real_text(raw, expected):
    assert clean_line(raw) == expected


def test_blank_lines_and_duplicates_are_dropped_or_flagged():
    raw = "1. Что такое индекс?\n\n   \n2. Что такое ИНДЕКС?\n3. Что такое JOIN?\n"
    parsed = parse(raw, set(), 10)
    assert [(p.text, p.status) for p in parsed] == [
        ("Что такое индекс?", "ok"),
        ("Что такое ИНДЕКС?", "duplicate"),
        ("Что такое JOIN?", "ok"),
    ]


def test_questions_already_in_the_bank_are_flagged_as_duplicates():
    parsed = parse("Что такое JOIN?\nЧто такое VIEW?", {"что такое join?"}, 10)
    assert [p.status for p in parsed] == ["duplicate", "ok"]


def test_long_lines_and_the_bank_limit_are_applied_in_order():
    raw = "\n".join(["a" * 11, "b", "c", "d"])
    parsed = parse(raw, set(), 2, max_length=10)
    assert [(p.text[:1], p.status) for p in parsed] == [
        ("a", "too_long"),
        ("b", "ok"),
        ("c", "ok"),
        ("d", "over_limit"),
    ]
    assert [p.status for p in parse("x\ny", set(), 0)] == ["over_limit", "over_limit"]


def test_the_exact_length_limit_is_allowed():
    assert parse("x" * 10, set(), 5, max_length=10)[0].status == "ok"


def test_there_are_at_least_three_builtin_presets_with_valid_criteria():
    assert len(PRESETS) >= 3
    assert {"practice", "coursework", "oral"} <= set(PRESET_KEYS)
    for preset in PRESETS:
        names = [n for n, _ in preset.criteria]
        assert len(names) == len(set(names)) <= settings.MAX_CRITERIA_PER_ASSIGNMENT
        assert all(1 <= points <= 100 for _, points in preset.criteria)


@pytest_asyncio.fixture
async def owner(client, make_teacher):
    headers = await make_teacher(f"qi_owner_{uuid4().hex[:10]}")
    assignment = (
        await client.post(f"{API}/assignments", headers=headers, json={"title": "Банк"})
    ).json()
    return headers, assignment


async def bank(client, headers, assignment):
    return (
        await client.get(f"{API}/assignments/{assignment['id']}/questions", headers=headers)
    ).json()


def import_url(assignment):
    return f"{API}/assignments/{assignment['id']}/questions/import"


@pytest.mark.asyncio
async def test_preview_changes_nothing_and_reports_every_line(client, owner):
    headers, assignment = owner
    response = await client.post(
        import_url(assignment),
        headers=headers,
        json={"text": "1. Первый?\n2. Второй?\n2. Второй?\n", "dry_run": True},
    )
    assert response.status_code == 200
    body = response.json()
    assert [(line["text"], line["status"]) for line in body["lines"]] == [
        ("Первый?", "ok"),
        ("Второй?", "ok"),
        ("Второй?", "duplicate"),
    ]
    assert body["added"] == 0
    assert body["remaining_capacity"] == settings.MAX_QUESTIONS_PER_ASSIGNMENT
    assert await bank(client, headers, assignment) == []


@pytest.mark.asyncio
async def test_adding_keeps_order_difficulty_and_topic(client, owner):
    headers, assignment = owner
    response = await client.post(
        import_url(assignment),
        headers=headers,
        json={
            "text": "- А?\n- Б?\n- В?",
            "difficulty": 3,
            "topic": "  SQL   основы ",
            "dry_run": False,
        },
    )
    assert response.status_code == 200 and response.json()["added"] == 3
    questions = await bank(client, headers, assignment)
    assert [(q["text"], q["difficulty"], q["topic"]) for q in questions] == [
        ("А?", 3, "SQL основы"),
        ("Б?", 3, "SQL основы"),
        ("В?", 3, "SQL основы"),
    ]
    assert [q["position"] for q in questions] == sorted(q["position"] for q in questions)
    again = await client.post(
        import_url(assignment), headers=headers, json={"text": "Б?\nГ?", "dry_run": False}
    )
    assert [line["status"] for line in again.json()["lines"]] == ["duplicate", "ok"]
    assert len(await bank(client, headers, assignment)) == 4


@pytest.mark.asyncio
async def test_the_bank_limit_cuts_the_list(client, owner, monkeypatch):
    headers, assignment = owner
    monkeypatch.setattr(settings, "MAX_QUESTIONS_PER_ASSIGNMENT", 3)
    response = await client.post(
        import_url(assignment),
        headers=headers,
        json={"text": "1?\n2?\n3?\n4?\n5?", "dry_run": False},
    )
    body = response.json()
    assert [line["status"] for line in body["lines"]] == ["ok"] * 3 + ["over_limit"] * 2
    assert (body["added"], body["remaining_capacity"]) == (3, 0)
    assert len(await bank(client, headers, assignment)) == 3


@pytest.mark.asyncio
async def test_a_too_long_question_is_flagged_not_saved(client, owner):
    headers, assignment = owner
    response = await client.post(
        import_url(assignment),
        headers=headers,
        json={"text": "ок?\n" + "я" * 1001, "dry_run": False},
    )
    assert [line["status"] for line in response.json()["lines"]] == ["ok", "too_long"]
    assert [q["text"] for q in await bank(client, headers, assignment)] == ["ок?"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"text": ""},
        {"text": "x" * 50001},
        {"text": "x", "difficulty": 4},
        {"text": "x", "difficulty": 0},
        {"text": "x", "topic": "t" * 61},
    ],
)
async def test_bad_import_requests_are_validation_errors(client, owner, body):
    headers, assignment = owner
    response = await client.post(import_url(assignment), headers=headers, json=body)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_only_the_owner_can_import_or_apply_presets(client, owner, make_teacher):
    headers, assignment = owner
    other = await make_teacher(f"qi_other_{uuid4().hex[:10]}")
    body = {"text": "Вопрос?", "dry_run": False}
    assert (await client.post(import_url(assignment), headers=other, json=body)).status_code == 404
    assert (await client.post(import_url(assignment), json=body)).status_code == 401
    preset_url = f"{API}/assignments/{assignment['id']}/criteria/preset"
    assert (
        await client.post(preset_url, headers=other, json={"preset": "practice"})
    ).status_code == 404
    assert (await client.post(preset_url, json={"preset": "practice"})).status_code == 401
    assert (await client.get(f"{API}/criteria-presets")).status_code == 401
    assert await bank(client, headers, assignment) == []


@pytest.mark.asyncio
async def test_presets_are_listed_and_do_not_touch_the_database(client, owner):
    headers, _ = owner
    presets = (await client.get(f"{API}/criteria-presets", headers=headers)).json()
    assert [p["key"] for p in presets][:3] == ["practice", "coursework", "oral"]
    assert all(p["criteria"] and p["title"] for p in presets)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", PRESET_KEYS)
async def test_every_preset_fills_criteria_that_can_then_be_edited(client, make_teacher, key):
    headers = await make_teacher(f"qi_preset_{key}")
    assignment = (
        await client.post(f"{API}/assignments", headers=headers, json={"title": "Работа"})
    ).json()
    response = await client.post(
        f"{API}/assignments/{assignment['id']}/criteria/preset",
        headers=headers,
        json={"preset": key},
    )
    assert response.status_code == 200
    expected = next(p for p in PRESETS if p.key == key).criteria
    assert [(c["name"], c["max_points"]) for c in response.json()] == list(expected)
    first = response.json()[0]
    edited = await client.patch(
        f"{API}/assignments/{assignment['id']}/criteria/{first['id']}",
        headers=headers,
        json={"max_points": 7},
    )
    assert edited.status_code == 200 and edited.json()["max_points"] == 7


@pytest.mark.asyncio
async def test_applying_a_preset_twice_adds_nothing_new(client, owner):
    headers, assignment = owner
    url = f"{API}/assignments/{assignment['id']}/criteria/preset"
    first = await client.post(url, headers=headers, json={"preset": "oral"})
    again = await client.post(url, headers=headers, json={"preset": "oral"})
    assert first.json() == again.json()
    mixed = await client.post(url, headers=headers, json={"preset": "practice"})
    assert len(mixed.json()) == len(first.json()) + len(PRESETS[0].criteria)


@pytest.mark.asyncio
async def test_unknown_preset_and_locked_or_full_assignments_are_refused(
    client, owner, monkeypatch
):
    headers, assignment = owner
    url = f"{API}/assignments/{assignment['id']}/criteria/preset"
    unknown = await client.post(url, headers=headers, json={"preset": "nope"})
    assert unknown.status_code == 404 and unknown.json()["code"] == "preset_not_found"
    monkeypatch.setattr(settings, "MAX_CRITERIA_PER_ASSIGNMENT", 2)
    full = await client.post(url, headers=headers, json={"preset": "coursework"})
    assert full.status_code == 409 and full.json()["code"] == "criteria_limit"
    assert "лимит" in full.json()["detail"]
    assert (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=headers)
    ).json() == []


@pytest.mark.asyncio
async def test_presets_are_refused_after_the_first_submission(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("qi_locked")
    assignment = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": "Работа", "questions_per_submission": 1},
        )
    ).json()
    await client.post(
        f"{API}/assignments/{assignment['id']}/criteria",
        headers=headers,
        json={"name": "Код", "max_points": 5},
    )
    code = await make_session(headers)
    await client.put(
        f"{API}/sessions/{code}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    await join(code, "Анна")
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    response = await client.post(
        f"{API}/assignments/{assignment['id']}/criteria/preset",
        headers=headers,
        json={"preset": "oral"},
    )
    assert response.status_code == 409 and response.json()["code"] == "criteria_locked"


@pytest.mark.asyncio
async def test_concurrent_imports_never_duplicate_or_exceed_the_limit(client, owner, monkeypatch):
    headers, assignment = owner
    monkeypatch.setattr(settings, "MAX_QUESTIONS_PER_ASSIGNMENT", 5)
    results = await asyncio.gather(
        *(
            client.post(
                import_url(assignment),
                headers=headers,
                json={"text": "\n".join(f"Вопрос {n}?" for n in range(4)), "dry_run": False},
            )
            for _ in range(6)
        )
    )
    assert all(r.status_code in (200, 409) for r in results)
    texts = [q["text"] for q in await bank(client, headers, assignment)]
    assert len(texts) == len(set(texts)) and len(texts) <= 5
    assert set(texts) <= {f"Вопрос {n}?" for n in range(4)}


@pytest.mark.asyncio
async def test_concurrent_presets_give_one_set_of_criteria(client, owner):
    headers, assignment = owner
    url = f"{API}/assignments/{assignment['id']}/criteria/preset"
    results = await asyncio.gather(
        *(client.post(url, headers=headers, json={"preset": "practice"}) for _ in range(6))
    )
    assert all(r.status_code == 200 for r in results)
    criteria = (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=headers)
    ).json()
    assert [c["name"] for c in criteria] == [n for n, _ in PRESETS[0].criteria]


@pytest.mark.asyncio
async def test_the_system_assignment_refuses_imports_and_presets(client, make_teacher):
    headers = await make_teacher("qi_system")
    session = (
        await client.post(
            f"{API}/sessions", headers=headers, json={"title": "Б", "session_mode": "quick"}
        )
    ).json()
    aid = session["assignment_id"]
    imported = await client.post(
        f"{API}/assignments/{aid}/questions/import", headers=headers, json={"text": "Вопрос?"}
    )
    preset = await client.post(
        f"{API}/assignments/{aid}/criteria/preset", headers=headers, json={"preset": "oral"}
    )
    assert imported.status_code == 409 and preset.status_code == 409
