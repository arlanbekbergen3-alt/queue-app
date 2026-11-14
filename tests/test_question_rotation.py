import asyncio
import uuid
from collections import Counter

import pytest

from services.protocol_calls import choose_question_ids, draw_round
from tests.conftest import API


def test_session_usage_makes_unused_questions_come_first():
    pool = [f"q{i}" for i in range(6)]
    session_usage = {"q0": 1, "q1": 1, "q2": 1}
    for seed in ("a", "b", "c", "d", "e"):
        assert set(choose_question_ids(seed, pool, 3, None, session_usage)) == {"q3", "q4", "q5"}


def test_a_new_round_starts_from_the_least_used_questions():
    pool = [f"q{i}" for i in range(4)]
    session_usage = {"q0": 2, "q1": 1, "q2": 1, "q3": 2}
    chosen = choose_question_ids("s", pool, 3, None, session_usage)
    assert set(chosen[:2]) == {"q1", "q2"}
    assert chosen[2] in {"q0", "q3"}


def test_the_students_own_history_comes_before_the_session_usage():
    pool = [f"q{i}" for i in range(4)]
    usage = {"q0": 1, "q1": 1}
    session_usage = {"q2": 5, "q3": 5}
    for seed in ("a", "b", "c", "d"):
        assert set(choose_question_ids(seed, pool, 2, usage, session_usage)) == {"q2", "q3"}


def test_session_usage_breaks_ties_between_equally_familiar_questions():
    pool = [f"q{i}" for i in range(4)]
    usage = {"q0": 1, "q1": 1, "q2": 1, "q3": 1}
    session_usage = {"q0": 3, "q1": 3, "q2": 0, "q3": 0}
    assert set(choose_question_ids("s", pool, 2, usage, session_usage)) == {"q2", "q3"}


def test_asking_for_more_than_the_bank_returns_every_question_once():
    pool = ["a", "b"]
    assert sorted(choose_question_ids("s", pool, 5, None, {"a": 2})) == ["a", "b"]
    assert choose_question_ids("s", [], 3) == []


def test_the_draw_is_reproducible_from_the_recorded_inputs():
    pool = [f"q{i}" for i in range(9)]
    usage, session_usage = {"q1": 1}, {"q2": 2, "q3": 1}
    first = choose_question_ids("seed", pool, 4, usage, session_usage)
    assert first == choose_question_ids("seed", pool, 4, usage, session_usage)


def test_the_round_number_follows_the_least_used_question():
    pool = ["a", "b", "c"]
    assert draw_round(None, pool) == 1
    assert draw_round({"a": 1, "b": 1}, pool) == 1
    assert draw_round({"a": 1, "b": 1, "c": 1}, pool) == 2
    assert draw_round({"a": 3, "b": 2, "c": 2}, pool) == 3
    assert draw_round({}, []) == 1


class Room:
    def __init__(self, client, headers, assignment, criteria):
        self.client = client
        self.headers = headers
        self.assignment = assignment
        self.criteria = criteria

    async def open(self, group: str | None = None) -> str:
        body = {"title": "Сдача"}
        if group:
            body["group"] = group
        created = await self.client.post(f"{API}/sessions", headers=self.headers, json=body)
        code = created.json()["code"]
        attach = await self.client.put(
            f"{API}/sessions/{code}/assignment",
            headers=self.headers,
            json={"assignment_id": self.assignment["id"]},
        )
        assert attach.status_code == 204, attach.text
        return code

    async def join(self, code: str, name: str) -> None:
        response = await self.client.post(
            f"{API}/sessions/{code}/join", json={"name": name, "token": f"tok-{uuid.uuid4().hex}"}
        )
        assert response.status_code == 200, response.text

    async def serve(self, code: str) -> dict:
        called = await self.client.post(f"{API}/sessions/{code}/next", headers=self.headers)
        assert called.status_code == 200, called.text
        current = await self.client.get(
            f"{API}/sessions/{code}/submissions/current", headers=self.headers
        )
        submission = current.json()
        done = await self.client.post(
            f"{API}/submissions/{submission['id']}/complete",
            headers=self.headers,
            json={
                "scores": [{"criterion_id": c["id"], "points": 3} for c in self.criteria],
                "comment": "",
            },
        )
        assert done.status_code == 200, done.text
        audit = await self.client.get(f"{API}/submissions/{submission['id']}", headers=self.headers)
        return audit.json()

    async def events(self, code: str) -> list[dict]:
        response = await self.client.get(f"{API}/sessions/{code}/events-log", headers=self.headers)
        return response.json()


@pytest.fixture
def room(client, make_teacher):
    async def _room(username: str, *, questions: int = 6, per_submission: int = 2) -> Room:
        headers = await make_teacher(username)
        created = await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {username}", "questions_per_submission": per_submission},
        )
        assignment = created.json()
        for i in range(questions):
            await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": f"Вопрос {i}", "difficulty": 2, "topic": "t"},
            )
        criteria = []
        for name in ("Код", "Защита"):
            made = await client.post(
                f"{API}/assignments/{assignment['id']}/criteria",
                headers=headers,
                json={"name": name, "max_points": 5},
            )
            criteria.append(made.json())
        return Room(client, headers, assignment, criteria)

    return _room


def texts(submission: dict) -> list[str]:
    return [q["text"] for q in submission["questions"]]


@pytest.mark.asyncio
async def test_questions_do_not_repeat_in_a_session_until_the_bank_is_used(room):
    r = await room("rot_round", questions=6, per_submission=2)
    code = await r.open()
    for n in range(6):
        await r.join(code, f"Студент {n}")

    seen: list[str] = []
    for _ in range(3):
        seen += texts(await r.serve(code))
    assert len(seen) == len(set(seen)) == 6

    for _ in range(3):
        seen += texts(await r.serve(code))
    assert Counter(seen) == {f"Вопрос {i}": 2 for i in range(6)}


@pytest.mark.asyncio
async def test_a_bank_that_does_not_divide_evenly_stays_balanced(room):
    r = await room("rot_balance", questions=5, per_submission=2)
    code = await r.open()
    for n in range(7):
        await r.join(code, f"Студент {n}")

    counts: Counter[str] = Counter()
    for _ in range(7):
        counts.update(texts(await r.serve(code)))
        full = [counts.get(f"Вопрос {i}", 0) for i in range(5)]
        assert max(full) - min(full) <= 1, full


@pytest.mark.asyncio
async def test_every_draw_is_written_to_the_journal_without_names(room):
    r = await room("rot_journal", questions=4, per_submission=2)
    code = await r.open()
    for n in range(3):
        await r.join(code, f"Секретная Фамилия {n}")
    for _ in range(3):
        await r.serve(code)

    events = await r.events(code)
    draws = [e for e in events if e["kind"] == "questions_drawn"]

    assert len(draws) == 3
    assert [d["details"]["round"] for d in draws] == [1, 1, 2]
    assert all(len(d["details"]["chosen"]) == 2 and d["details"]["pool"] == 4 for d in draws)
    assert all(d["details"]["short"] is False for d in draws)
    assert "Секретная" not in str(events)


@pytest.mark.asyncio
async def test_a_bank_smaller_than_needed_is_visible_everywhere(room):
    r = await room("rot_short", questions=2, per_submission=3)
    code = await r.open()
    await r.join(code, "Анна Иванова")

    listing = (await r.client.get(f"{API}/assignments", headers=r.headers)).json()
    assignment = next(a for a in listing if a["id"] == r.assignment["id"])
    submission = await r.serve(code)
    draw = next(e for e in await r.events(code) if e["kind"] == "questions_drawn")

    assert (assignment["question_count"], assignment["questions_per_submission"]) == (2, 3)
    assert submission["questions_short"] is True and len(submission["questions"]) == 2
    assert draw["details"]["short"] is True and draw["details"]["wanted"] == 3


@pytest.mark.asyncio
async def test_a_retake_avoids_the_questions_of_earlier_attempts(room):
    r = await room("rot_retake", questions=6, per_submission=3)
    attempts = []
    for _ in range(2):
        code = await r.open(group="ИС-22")
        await r.join(code, "Алла Иванова")
        attempts.append(set(texts(await r.serve(code))))

    assert attempts[0].isdisjoint(attempts[1])
    assert len(attempts[0] | attempts[1]) == 6


@pytest.mark.asyncio
async def test_a_third_attempt_reuses_the_least_used_questions_when_the_bank_is_spent(room):
    r = await room("rot_third", questions=4, per_submission=2)
    first_two: list[str] = []
    for _ in range(2):
        code = await r.open(group="ИС-23")
        await r.join(code, "Борис Петров")
        first_two += texts(await r.serve(code))
    assert sorted(first_two) == [f"Вопрос {i}" for i in range(4)]

    code = await r.open(group="ИС-23")
    await r.join(code, "Борис Петров")
    third = await r.serve(code)

    assert len(texts(third)) == 2 and len(set(texts(third))) == 2


@pytest.mark.asyncio
async def test_a_retakers_own_history_wins_over_what_the_session_already_used(room):
    r = await room("rot_priority", questions=4, per_submission=2)
    earlier = await r.open(group="ИС-24")
    await r.join(earlier, "Вера Смирнова")
    own = set(texts(await r.serve(earlier)))

    code = await r.open(group="ИС-24")
    await r.join(code, "Чужой Студент")
    await r.join(code, "Вера Смирнова")
    await r.serve(code)
    veras = await r.serve(code)

    assert set(texts(veras)) == {f"Вопрос {i}" for i in range(4)} - own


@pytest.mark.asyncio
async def test_the_audit_view_reproduces_the_draw_and_shows_the_round(room):
    r = await room("rot_replay", questions=6, per_submission=2)
    code = await r.open()
    for n in range(4):
        await r.join(code, f"Студент {n}")
    for _ in range(3):
        await r.serve(code)
    last = await r.serve(code)

    again = choose_question_ids(
        last["selection_seed"],
        last["pool_question_ids"],
        2,
        last["selection_usage"],
        last["selection_session_usage"],
    )
    by_id = {
        q["id"]: q["text"]
        for q in (
            await r.client.get(
                f"{API}/assignments/{r.assignment['id']}/questions", headers=r.headers
            )
        ).json()
    }

    assert [by_id[i] for i in again] == texts(last)
    assert last["selection_round"] == 2


@pytest.mark.asyncio
async def test_sessions_of_one_assignment_draw_in_parallel_without_interfering(room):
    r = await room("rot_parallel", questions=5, per_submission=3)
    codes = [await r.open() for _ in range(4)]
    for code in codes:
        await r.join(code, "Студент")

    responses = await asyncio.gather(
        *(r.client.post(f"{API}/sessions/{c}/next", headers=r.headers) for c in codes)
    )

    assert {x.status_code for x in responses} == {200}
    for code in codes:
        current = (
            await r.client.get(f"{API}/sessions/{code}/submissions/current", headers=r.headers)
        ).json()
        assert len(set(texts(current))) == 3
        draws = [e for e in await r.events(code) if e["kind"] == "questions_drawn"]
        assert len(draws) == 1


@pytest.mark.asyncio
async def test_parallel_calls_in_one_session_draw_once(room):
    r = await room("rot_one", questions=5, per_submission=2)
    code = await r.open()
    for n in range(5):
        await r.join(code, f"Студент {n}")

    responses = await asyncio.gather(
        *(r.client.post(f"{API}/sessions/{code}/next", headers=r.headers) for _ in range(8))
    )

    assert sorted(x.status_code for x in responses) == [200] + [409] * 7
    draws = [e for e in await r.events(code) if e["kind"] == "questions_drawn"]
    assert len(draws) == 1
