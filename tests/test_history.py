import asyncio
import uuid

import pytest

from services.history import add_deltas, percent_of
from services.identity import group_key, name_key
from services.protocol_calls import choose_question_ids

API = "/api/v1"


def test_name_key_ignores_case_spaces_and_yo():
    assert name_key("Алёна  Фёдорова") == name_key("алена федорова") == "аленафедорова"
    assert name_key(" Иван\tПетров ") == name_key("ИВАН ПЕТРОВ")
    assert name_key("Ａnna") == name_key("anna")


def test_name_key_does_not_merge_different_or_similar_names():
    assert name_key("Иванов Иван") != name_key("Иван Иванов")
    assert name_key("Иванов Иван") != name_key("Иванов Ивн")
    assert name_key("Anna") != name_key("Anna B")


def test_group_key_is_empty_without_a_group_and_normalised_otherwise():
    assert group_key(None) == group_key("") == ""
    assert group_key("ИС-22") == group_key(" ис-22 ")
    assert group_key("ИС-22") != group_key("ИС-23")


def test_percent_and_deltas():
    assert percent_of(8, 10) == 80.0
    assert percent_of(None, 10) is None
    assert percent_of(5, 0) is None
    assert add_deltas([60.0, 80.0, 70.0]) == [None, 20.0, -10.0]
    assert add_deltas([None, 50.0, None, 75.0]) == [None, None, None, 25.0]


def test_selection_prefers_questions_the_student_has_not_seen():
    pool = [f"q{i}" for i in range(6)]
    usage = {"q0": 1, "q1": 1, "q2": 1}
    for seed in ("a", "b", "c", "d", "e"):
        assert set(choose_question_ids(seed, pool, 3, usage)) == {"q3", "q4", "q5"}


def test_selection_mixes_tiers_and_takes_the_least_used_when_the_bank_runs_out():
    pool = [f"q{i}" for i in range(4)]
    usage = {"q0": 2, "q1": 1, "q2": 1, "q3": 0}
    chosen = choose_question_ids("s", pool, 3, usage)
    assert chosen[0] == "q3"
    assert set(chosen[1:]) == {"q1", "q2"}
    assert "q0" not in chosen
    assert len(set(choose_question_ids("s", pool, 4, usage))) == 4


def test_selection_without_history_matches_the_plain_random_choice():
    pool = [f"q{i}" for i in range(10)]
    assert choose_question_ids("seed", pool, 4) == choose_question_ids("seed", pool, 4, {})
    assert choose_question_ids("seed", pool, 4) == choose_question_ids(
        "seed", pool, 4, {"unrelated": 3}
    )


def test_selection_is_reproducible_from_the_seed_and_the_recorded_usage():
    pool = [f"q{i}" for i in range(8)]
    usage = {"q1": 1, "q4": 2}
    assert choose_question_ids("x", pool, 5, usage) == choose_question_ids("x", pool, 5, usage)


class World:
    def __init__(self, client, headers, assignment, criteria):
        self.client = client
        self.headers = headers
        self.assignment = assignment
        self.criteria = criteria
        self.sessions = 0

    async def session(self, group: str | None = None, assignment_id: str | None = None) -> str:
        self.sessions += 1
        body = {"title": f"S{self.sessions}"}
        if group:
            body["group"] = group
        r = await self.client.post(f"{API}/sessions", headers=self.headers, json=body)
        code = r.json()["code"]
        r = await self.client.put(
            f"{API}/sessions/{code}/assignment",
            headers=self.headers,
            json={"assignment_id": assignment_id or self.assignment["id"]},
        )
        assert r.status_code == 204, r.text
        return code

    async def join(self, code: str, name: str, token: str | None = None, device: str | None = None):
        token = token or f"tok-{uuid.uuid4().hex}"
        body = {"name": name, "token": token}
        if device:
            body["device_key"] = device
        r = await self.client.post(f"{API}/sessions/{code}/join", json=body)
        assert r.status_code == 200, r.text
        return {"token": token, "code": code, "name": name}

    async def call_current(self, code: str) -> dict:
        r = await self.client.post(f"{API}/sessions/{code}/next", headers=self.headers)
        assert r.status_code == 200, r.text
        r = await self.client.get(
            f"{API}/sessions/{code}/submissions/current", headers=self.headers
        )
        return r.json()

    async def grade(self, submission: dict, points: tuple[int, ...]) -> dict:
        body = {
            "scores": [
                {"criterion_id": c["id"], "points": p}
                for c, p in zip(self.criteria, points, strict=True)
            ],
            "comment": "",
        }
        r = await self.client.post(
            f"{API}/submissions/{submission['id']}/complete", headers=self.headers, json=body
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def take(
        self,
        name: str,
        points: tuple[int, ...] = (5, 5),
        *,
        group: str | None = None,
        device: str | None = None,
        assignment_id: str | None = None,
    ) -> dict:
        code = await self.session(group, assignment_id)
        student = await self.join(code, name, device=device)
        current = await self.call_current(code)
        done = await self.grade(current, points)
        return {**student, "submission": done}

    async def students(self, **params) -> list[dict]:
        r = await self.client.get(f"{API}/students", headers=self.headers, params=params)
        assert r.status_code == 200, r.text
        return r.json()

    async def card(self, student_id: str, **params) -> dict:
        r = await self.client.get(
            f"{API}/students/{student_id}", headers=self.headers, params=params
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def my_history(self, student: dict) -> dict:
        r = await self.client.get(
            f"{API}/sessions/{student['code']}/me/history",
            headers={"X-Entry-Token": student["token"]},
        )
        assert r.status_code == 200, r.text
        return r.json()


@pytest.fixture
def world(client, make_teacher):
    async def _world(username: str, *, questions: int = 6, per_submission: int = 3) -> World:
        headers = await make_teacher(username)
        r = await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {username}", "questions_per_submission": per_submission},
        )
        assignment = r.json()
        for i in range(questions):
            await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": f"Вопрос {i}", "difficulty": 2, "topic": "t"},
            )
        criteria = []
        for name in ("Код", "Защита"):
            r = await client.post(
                f"{API}/assignments/{assignment['id']}/criteria",
                headers=headers,
                json={"name": name, "max_points": 5},
            )
            criteria.append(r.json())
        return World(client, headers, assignment, criteria)

    return _world


def question_texts(submission: dict) -> set[str]:
    return {q["text"] for q in submission["questions"]}


@pytest.mark.asyncio
async def test_the_same_person_is_linked_across_sessions_despite_case_spaces_and_yo(world):
    w = await world("hist_link")
    await w.take("Алёна Фёдорова", group="ИС-22")
    await w.take("  алена   федорова ", group=" ис-22")
    await w.take("АЛЕНА ФЕДОРОВА", group="ИС-22")

    students = await w.students()

    assert len(students) == 1
    assert students[0]["submissions"] == 3
    assert students[0]["name"] == "Алёна Фёдорова"
    card = await w.card(students[0]["id"])
    attempts = card["assignments"][0]["attempts"]
    assert [a["attempt_number"] for a in attempts] == [1, 2, 3]
    assert len(card["names"]) == 3


@pytest.mark.asyncio
async def test_different_groups_and_similar_names_are_never_linked_automatically(world):
    w = await world("hist_nolink")
    await w.take("Иван Петров", group="ИС-22")
    await w.take("Иван Петров", group="ИС-23")
    await w.take("Иван Петров")
    await w.take("Иван Петро")
    await w.take("Петров Иван", group="ИС-22")

    students = await w.students()

    assert len(students) == 5
    assert all(s["submissions"] == 1 for s in students)
    assert [s["name"] for s in await w.students(group="ис-22")] == ["Иван Петров", "Петров Иван"]
    assert len(await w.students(q="  ПЕТРОВ")) == 4


@pytest.mark.asyncio
async def test_students_of_different_teachers_are_separate_identities(world):
    a = await world("hist_t_a")
    b = await world("hist_t_b")
    await a.take("Анна", group="ИС")
    await b.take("Анна", group="ИС")

    (sa,) = await a.students()
    (sb,) = await b.students()

    assert sa["id"] != sb["id"]
    assert sa["submissions"] == sb["submissions"] == 1


@pytest.mark.asyncio
async def test_retake_gets_questions_the_student_has_not_had_before(world):
    w = await world("hist_retake", questions=6, per_submission=3)
    first = await w.take("Анна")
    second = await w.take("Анна")

    assert question_texts(first["submission"]).isdisjoint(question_texts(second["submission"]))
    assert second["submission"]["attempt_number"] == 2
    assert first["submission"]["attempt_number"] == 1
    r = await w.client.get(f"{API}/submissions/{second['submission']['id']}", headers=w.headers)
    body = r.json()
    assert len(body["selection_usage"]) == 3
    assert set(body["selection_usage"].values()) == {1}
    assert first["submission"]["selection_usage"] is None


@pytest.mark.asyncio
async def test_retake_choice_is_reproducible_from_the_stored_seed_and_usage(world):
    w = await world("hist_repro", questions=6, per_submission=3)
    await w.take("Анна")
    second = await w.take("Анна")
    r = await w.client.get(f"{API}/submissions/{second['submission']['id']}", headers=w.headers)
    body = r.json()
    texts = {
        q["id"]: q["text"]
        for q in (
            await w.client.get(
                f"{API}/assignments/{w.assignment['id']}/questions", headers=w.headers
            )
        ).json()
    }

    again = choose_question_ids(
        body["selection_seed"], body["pool_question_ids"], 3, body["selection_usage"]
    )

    assert [texts[i] for i in again] == [q["text"] for q in body["questions"]]


@pytest.mark.asyncio
async def test_third_attempt_uses_the_least_used_questions_when_the_bank_is_exhausted(world):
    w = await world("hist_exhaust", questions=6, per_submission=3)
    runs = [await w.take("Анна") for _ in range(4)]

    seen: dict[str, int] = {}
    for run in runs:
        for text in question_texts(run["submission"]):
            seen[text] = seen.get(text, 0) + 1

    assert [r["submission"]["attempt_number"] for r in runs] == [1, 2, 3, 4]
    assert sum(seen.values()) == 12
    assert max(seen.values()) == 2 and min(seen.values()) == 2
    assert question_texts(runs[0]["submission"]).isdisjoint(question_texts(runs[1]["submission"]))
    assert question_texts(runs[2]["submission"]).isdisjoint(question_texts(runs[3]["submission"]))


@pytest.mark.asyncio
async def test_a_small_bank_repeats_questions_instead_of_failing(world):
    w = await world("hist_small", questions=3, per_submission=3)
    first = await w.take("Анна")
    second = await w.take("Анна")

    assert question_texts(first["submission"]) == question_texts(second["submission"])
    assert second["submission"]["attempt_number"] == 2


@pytest.mark.asyncio
async def test_other_students_do_not_affect_a_students_questions(world):
    w = await world("hist_others", questions=6, per_submission=3)
    await w.take("Анна")
    other = await w.take("Борис")

    assert other["submission"]["attempt_number"] == 1
    assert other["submission"]["selection_usage"] is None


@pytest.mark.asyncio
async def test_a_cancelled_call_does_not_use_up_an_attempt_or_questions(world):
    w = await world("hist_cancel", questions=6, per_submission=3)
    code = await w.session()
    student = await w.join(code, "Анна")
    await w.call_current(code)
    await w.client.post(f"{API}/sessions/{code}/leave", headers={"X-Entry-Token": student["token"]})

    retry = await w.take("Анна")

    assert retry["submission"]["attempt_number"] == 1
    assert retry["submission"]["selection_usage"] is None
    (s,) = await w.students()
    assert s["submissions"] == 1


@pytest.mark.asyncio
async def test_a_student_who_was_skipped_and_returned_keeps_attempt_and_questions(world):
    w = await world("hist_reopen", questions=6, per_submission=3)
    await w.take("Анна")
    code = await w.session()
    student = await w.join(code, "Анна")
    current = await w.call_current(code)
    entry = current["entry_id"]
    await w.client.post(f"{API}/sessions/{code}/entries/{entry}/skip", headers=w.headers)
    await w.client.post(f"{API}/sessions/{code}/entries/{entry}/requeue", headers=w.headers)
    again = await w.call_current(code)

    assert again["id"] == current["id"]
    assert again["attempt_number"] == 2
    assert question_texts(again) == question_texts(current)
    assert student["name"] == "Анна"


@pytest.mark.asyncio
async def test_card_shows_scores_questions_dates_and_dynamics_between_attempts(world):
    w = await world("hist_card")
    await w.take("Анна", points=(3, 3))
    await w.take("Анна", points=(5, 4))
    await w.take("Анна", points=(4, 3))
    (student,) = await w.students()

    card = await w.card(student["id"])

    (assignment,) = card["assignments"]
    assert assignment["title"] == w.assignment["title"]
    attempts = assignment["attempts"]
    assert [a["percent"] for a in attempts] == [60.0, 90.0, 70.0]
    assert [a["delta_percent"] for a in attempts] == [None, 30.0, -20.0]
    assert [(s["name"], s["points"], s["max_points"]) for s in attempts[1]["scores"]] == [
        ("Код", 5, 5),
        ("Защита", 4, 5),
    ]
    assert all(len(a["questions"]) == 3 for a in attempts)
    assert all(a["started_at"] and a["ended_at"] and a["session_code"] for a in attempts)
    assert sum(q["times"] for q in assignment["seen_questions"]) == 9
    assert (attempts[0]["total_points"], attempts[0]["max_points"]) == (6, 10)


@pytest.mark.asyncio
async def test_card_can_be_limited_to_one_assignment(world, client):
    w = await world("hist_filter")
    await w.take("Анна")
    other = (
        await client.post(
            f"{API}/assignments",
            headers=w.headers,
            json={"title": "Другая", "questions_per_submission": 1},
        )
    ).json()
    await client.post(
        f"{API}/assignments/{other['id']}/questions", headers=w.headers, json={"text": "Один"}
    )
    await client.post(
        f"{API}/assignments/{other['id']}/criteria",
        headers=w.headers,
        json={"name": "Всё", "max_points": 5},
    )
    code = await w.session(assignment_id=other["id"])
    await w.join(code, "Анна")
    current = await w.call_current(code)
    await w.client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=w.headers,
        json={
            "scores": [
                {
                    "criterion_id": (
                        await w.client.get(
                            f"{API}/assignments/{other['id']}/criteria", headers=w.headers
                        )
                    ).json()[0]["id"],
                    "points": 4,
                }
            ]
        },
    )
    (student,) = await w.students()

    full = await w.card(student["id"])
    only = await w.card(student["id"], assignment_id=other["id"])
    unknown = await client.get(
        f"{API}/students/{student['id']}", headers=w.headers, params={"assignment_id": "nope"}
    )

    assert len(full["assignments"]) == 2
    assert [a["title"] for a in only["assignments"]] == ["Другая"]
    assert only["assignments"][0]["attempts"][0]["attempt_number"] == 1
    assert unknown.status_code == 404
    assert len(await w.students(assignment_id=other["id"])) == 1
    assert (
        await client.get(f"{API}/students", headers=w.headers, params={"assignment_id": "nope"})
    ).status_code == 404


@pytest.mark.asyncio
async def test_merge_combines_two_records_and_renumbers_attempts(world):
    w = await world("hist_merge")
    await w.take("Вася Пупкин", points=(2, 2))
    await w.take("Василий Пупкин", points=(5, 5))
    first, second = await w.students()

    r = await w.client.post(
        f"{API}/students/{first['id']}/merge",
        headers=w.headers,
        json={"into_student_id": second["id"]},
    )

    assert r.status_code == 200, r.text
    card = r.json()
    assert card["id"] == second["id"]
    attempts = card["assignments"][0]["attempts"]
    assert sorted(a["attempt_number"] for a in attempts) == [1, 2]
    assert [a["percent"] for a in attempts] in ([40.0, 100.0], [100.0, 40.0])
    assert set(card["names"]) == {"Вася Пупкин", "Василий Пупкин"}
    assert [s["id"] for s in await w.students()] == [second["id"]]
    again = await w.take("ВАСЯ пупкин")
    assert again["submission"]["attempt_number"] == 3
    assert len(await w.students()) == 1


@pytest.mark.asyncio
async def test_merge_validates_its_input(world, make_teacher, client):
    w = await world("hist_merge_bad")
    await w.take("Анна")
    await w.take("Борис")
    a, b = await w.students()
    await w.take("Клара")
    c = next(s for s in await w.students() if s["name"] == "Клара")
    other = await make_teacher("hist_merge_other")

    same = await client.post(
        f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": a["id"]}
    )
    foreign = await client.post(
        f"{API}/students/{a['id']}/merge", headers=other, json={"into_student_id": b["id"]}
    )
    missing = await client.post(
        f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": "nope"}
    )
    await client.post(
        f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": b["id"]}
    )
    already = await client.post(
        f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": c["id"]}
    )
    into_merged = await client.post(
        f"{API}/students/{c['id']}/merge", headers=w.headers, json={"into_student_id": a["id"]}
    )

    assert (same.status_code, same.json()["code"]) == (409, "merge_same")
    assert foreign.status_code == 404
    assert (missing.status_code, missing.json()["code"]) == (404, "student_not_found")
    assert (already.status_code, already.json()["code"]) == (409, "student_already_merged")
    assert (into_merged.status_code, into_merged.json()["code"]) == (409, "student_already_merged")


@pytest.mark.asyncio
async def test_split_moves_one_submission_to_a_new_identity_and_renumbers(world):
    w = await world("hist_split")
    await w.take("Иван Иванов", points=(5, 5))
    namesake = await w.take("Иван Иванов", points=(1, 1))
    (student,) = await w.students()
    assert (await w.card(student["id"]))["assignments"][0]["attempts"][1]["attempt_number"] == 2

    r = await w.client.post(
        f"{API}/submissions/{namesake['submission']['id']}/student",
        headers=w.headers,
        json={"student_id": None},
    )

    assert r.status_code == 200, r.text
    new_card = r.json()
    assert new_card["id"] != student["id"]
    assert len(new_card["assignments"][0]["attempts"]) == 1
    assert new_card["assignments"][0]["attempts"][0]["attempt_number"] == 1
    old_card = await w.card(student["id"])
    assert len(old_card["assignments"][0]["attempts"]) == 1
    assert old_card["assignments"][0]["attempts"][0]["percent"] == 100.0
    assert len(await w.students()) == 2
    later = await w.take("Иван Иванов")
    assert later["submission"]["attempt_number"] == 2
    assert (await w.card(student["id"]))["assignments"][0]["attempts"][1]["attempt_number"] == 2


@pytest.mark.asyncio
async def test_split_submission_can_be_attached_to_an_existing_identity(world, client):
    w = await world("hist_attach")
    await w.take("Анна")
    stray = await w.take("Анна К.")
    a = next(s for s in await w.students() if s["name"] == "Анна")

    r = await client.post(
        f"{API}/submissions/{stray['submission']['id']}/student",
        headers=w.headers,
        json={"student_id": a["id"]},
    )

    assert r.status_code == 200
    attempts = r.json()["assignments"][0]["attempts"]
    assert sorted(x["attempt_number"] for x in attempts) == [1, 2]
    assert len(await w.students()) == 2
    same = await client.post(
        f"{API}/submissions/{stray['submission']['id']}/student",
        headers=w.headers,
        json={"student_id": a["id"]},
    )
    assert same.status_code == 200
    assert sorted(x["attempt_number"] for x in same.json()["assignments"][0]["attempts"]) == [1, 2]


@pytest.mark.asyncio
async def test_reassign_rejects_foreign_missing_and_merged_targets(world, make_teacher, client):
    w = await world("hist_reassign_bad")
    run = await w.take("Анна")
    await w.take("Борис")
    await w.take("Клара")
    anna, boris, clara = await w.students()
    await client.post(
        f"{API}/students/{boris['id']}/merge",
        headers=w.headers,
        json={"into_student_id": clara["id"]},
    )
    stranger = await make_teacher("hist_reassign_other")
    url = f"{API}/submissions/{run['submission']['id']}/student"

    foreign = await client.post(url, headers=stranger, json={"student_id": anna["id"]})
    missing = await client.post(url, headers=w.headers, json={"student_id": "nope"})
    merged = await client.post(url, headers=w.headers, json={"student_id": boris["id"]})
    nowhere = await client.post(
        f"{API}/submissions/nope/student", headers=w.headers, json={"student_id": None}
    )

    assert foreign.status_code == 404
    assert (missing.status_code, missing.json()["code"]) == (404, "student_not_found")
    assert (merged.status_code, merged.json()["code"]) == (409, "student_already_merged")
    assert nowhere.status_code == 404


@pytest.mark.asyncio
async def test_another_teacher_cannot_read_or_change_a_students_history(
    world, make_teacher, client
):
    w = await world("hist_owner")
    run = await w.take("Анна")
    (student,) = await w.students()
    stranger = await make_teacher("hist_owner_other")

    card = await client.get(f"{API}/students/{student['id']}", headers=stranger)
    listing = await client.get(f"{API}/students", headers=stranger)
    reassign = await client.post(
        f"{API}/submissions/{run['submission']['id']}/student", headers=stranger, json={}
    )
    anonymous = [
        await client.get(f"{API}/students"),
        await client.get(f"{API}/students/{student['id']}"),
        await client.post(f"{API}/students/{student['id']}/merge", json={"into_student_id": "x"}),
        await client.post(f"{API}/submissions/{run['submission']['id']}/student", json={}),
    ]

    assert card.status_code == 404
    assert listing.json() == []
    assert reassign.status_code == 404
    assert [r.status_code for r in anonymous] == [401, 401, 401, 401]


@pytest.mark.asyncio
async def test_student_sees_only_their_own_history_on_their_own_device(world, client):
    w = await world("hist_self")
    device_a, device_b = "A" * 24, "B" * 24
    await w.take("Анна", points=(3, 3), device=device_a)
    await w.take("Борис", points=(5, 5), device=device_b)
    code = await w.session()
    anna = await w.join(code, "Анна", device=device_a)
    boris = await w.join(code, "Борис", device=device_b)
    impostor = await w.join(code, "анна ", token="impostor-token-12345", device="C" * 24)
    legacy = await w.join(code, "АННА", token="legacy-token-1234567")

    assert [a["percent"] for a in (await w.my_history(anna))["attempts"]] == [60.0]
    assert [a["percent"] for a in (await w.my_history(boris))["attempts"]] == [100.0]
    assert (await w.my_history(impostor))["attempts"] == []
    assert (await w.my_history(legacy))["attempts"] == []
    body = (await w.my_history(anna))["attempts"][0]
    assert set(body) == {
        "assignment_title",
        "attempt_number",
        "ended_at",
        "total_points",
        "max_points",
        "bonus_points",
        "percent",
        "delta_percent",
        "comment",
        "scores",
        "questions",
    }
    assert "Борис" not in str(await w.my_history(anna))
    teacher_view = (await client.get(f"{API}/students", headers=w.headers)).json()
    assert len(teacher_view) == 2


@pytest.mark.asyncio
async def test_student_history_grows_with_attempts_and_shows_dynamics(world):
    w = await world("hist_self_dyn")
    device = "D" * 24
    await w.take("Анна", points=(3, 3), device=device)
    second = await w.take("Анна", points=(5, 4), device=device)

    history = await w.my_history(second)

    assert [
        (a["attempt_number"], a["percent"], a["delta_percent"]) for a in history["attempts"]
    ] == [
        (1, 60.0, None),
        (2, 90.0, 30.0),
    ]
    assert history["attempts"][1]["scores"][0] == {"name": "Код", "points": 5, "max_points": 5}


@pytest.mark.asyncio
async def test_student_history_hides_unfinished_and_cancelled_attempts(world):
    w = await world("hist_self_hidden")
    device = "E" * 24
    await w.take("Анна", device=device)
    code = await w.session()
    student = await w.join(code, "Анна", device=device)
    await w.call_current(code)

    assert len((await w.my_history(student))["attempts"]) == 1


@pytest.mark.asyncio
async def test_student_history_requires_a_valid_entry_token_for_that_session(world, client):
    w = await world("hist_self_auth")
    anna = await w.take("Анна", device="F" * 24)
    other_code = await w.session()
    boris = await w.join(other_code, "Борис", device="G" * 24)

    no_token = await client.get(f"{API}/sessions/{anna['code']}/me/history")
    wrong = await client.get(
        f"{API}/sessions/{anna['code']}/me/history", headers={"X-Entry-Token": boris["token"]}
    )
    unknown = await client.get(
        f"{API}/sessions/ZZZZZZ/me/history", headers={"X-Entry-Token": boris["token"]}
    )
    teacher_token_only = await client.get(
        f"{API}/sessions/{anna['code']}/me/history", headers=w.headers
    )

    assert no_token.status_code == 401
    assert wrong.status_code == 404
    assert unknown.status_code == 404
    assert teacher_token_only.status_code == 401


@pytest.mark.asyncio
async def test_merged_history_is_visible_to_the_student_only_for_the_same_device(world):
    w = await world("hist_self_merge")
    device = "H" * 24
    await w.take("Вася", points=(3, 3), device=device)
    second = await w.take("Василий", points=(5, 5), device=device)
    a, b = await w.students()
    await w.client.post(
        f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": b["id"]}
    )

    history = await w.my_history(second)

    assert sorted(x["attempt_number"] for x in history["attempts"]) == [1, 2]


@pytest.mark.asyncio
async def test_new_identity_creation_survives_parallel_joins_of_the_same_name(world):
    w = await world("hist_parallel_join")
    codes = [await w.session() for _ in range(6)]

    await asyncio.gather(*(w.join(code, "Анна Иванова") for code in codes))

    students = await w.students()
    assert len(students) == 1


@pytest.mark.asyncio
async def test_opposite_merges_at_the_same_time_succeed_only_once(world, client):
    w = await world("hist_race_merge")
    await w.take("Анна")
    await w.take("Борис")
    a, b = await w.students()

    results = await asyncio.gather(
        client.post(
            f"{API}/students/{a['id']}/merge", headers=w.headers, json={"into_student_id": b["id"]}
        ),
        client.post(
            f"{API}/students/{b['id']}/merge", headers=w.headers, json={"into_student_id": a["id"]}
        ),
    )

    codes = sorted(r.status_code for r in results)
    assert codes == [200, 409]
    assert len(await w.students()) == 1
    (only,) = await w.students()
    attempts = (await w.card(only["id"]))["assignments"][0]["attempts"]
    assert sorted(x["attempt_number"] for x in attempts) == [1, 2]


@pytest.mark.asyncio
async def test_merge_racing_a_new_call_keeps_attempt_numbers_consistent(world, client):
    for round_no in range(4):
        w = await world(f"hist_race_call_{round_no}")
        await w.take("Анна")
        await w.take("Анна К")
        a, b = sorted(await w.students(), key=lambda s: s["name"])
        code = await w.session()
        await w.join(code, "Анна")
        await w.join(code, "Анна К", token="second-token-0123456789")

        merged, called = await asyncio.gather(
            client.post(
                f"{API}/students/{b['id']}/merge",
                headers=w.headers,
                json={"into_student_id": a["id"]},
            ),
            client.post(f"{API}/sessions/{code}/next", headers=w.headers),
        )

        assert merged.status_code == 200 and called.status_code == 200
        card = await w.card(a["id"])
        numbers = sorted(
            x["attempt_number"] for x in card["assignments"][0]["attempts"] if x["attempt_number"]
        )
        assert numbers == list(range(1, len(numbers) + 1))


@pytest.mark.asyncio
async def test_history_actions_leave_the_audit_log_untouched(world, db_session):
    from sqlalchemy import text

    w = await world("hist_audit")
    await w.take("Анна")
    run = await w.take("Анна")
    before = (await db_session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
    (student,) = await w.students()
    await w.client.post(
        f"{API}/submissions/{run['submission']['id']}/student", headers=w.headers, json={}
    )
    after = (await db_session.execute(text("SELECT count(*) FROM audit_log"))).scalar_one()
    audit = await w.client.get(
        f"{API}/submissions/{run['submission']['id']}/audit", headers=w.headers
    )

    assert before == after
    assert student["id"]
    assert {e["action"] for e in audit.json()} == {"complete"}


@pytest.mark.asyncio
async def test_validation_of_history_requests(world, client):
    w = await world("hist_validation")
    assert (
        await client.post(f"{API}/students/x/merge", headers=w.headers, json={})
    ).status_code == 422
    assert (
        await client.post(
            f"{API}/students/x/merge", headers=w.headers, json={"into_student_id": ""}
        )
    ).status_code == 422
    assert (await client.get(f"{API}/students/nope", headers=w.headers)).status_code == 404
