import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from services.protocol_calls import choose_question_ids

API = "/api/v1"


@pytest.fixture
def setup(client, make_teacher, make_session, join):
    async def _setup(
        username: str,
        *,
        questions: int = 6,
        criteria: tuple[tuple[str, int], ...] = (("Код", 5), ("Защита", 5)),
        students: int = 1,
        per_submission: int = 3,
    ):
        headers = await make_teacher(username)
        response = await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {username}", "questions_per_submission": per_submission},
        )
        assert response.status_code == 201, response.text
        assignment = response.json()
        for i in range(questions):
            r = await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": f"Вопрос {i}", "difficulty": 1 + i % 3, "topic": "t"},
            )
            assert r.status_code == 201, r.text
        crit = []
        for name, points in criteria:
            r = await client.post(
                f"{API}/assignments/{assignment['id']}/criteria",
                headers=headers,
                json={"name": name, "max_points": points},
            )
            assert r.status_code == 201, r.text
            crit.append(r.json())
        code = await make_session(headers)
        r = await client.put(
            f"{API}/sessions/{code}/assignment",
            headers=headers,
            json={"assignment_id": assignment["id"]},
        )
        assert r.status_code == 204, r.text
        joined = [await join(code, f"{username}-s{i}") for i in range(students)]
        return {
            "headers": headers,
            "assignment": assignment,
            "criteria": crit,
            "code": code,
            "students": joined,
        }

    return _setup


async def call_next(client, ctx) -> dict:
    r = await client.post(f"{API}/sessions/{ctx['code']}/next", headers=ctx["headers"])
    assert r.status_code == 200, r.text
    r = await client.get(
        f"{API}/sessions/{ctx['code']}/submissions/current", headers=ctx["headers"]
    )
    assert r.status_code == 200, r.text
    return r.json()


def full_scores(ctx, points=None):
    return {
        "scores": [
            {"criterion_id": c["id"], "points": c["max_points"] if points is None else points}
            for c in ctx["criteria"]
        ],
        "comment": "ok",
    }


@pytest.mark.asyncio
async def test_validation_errors_name_field_and_limit(client, make_teacher):
    headers = await make_teacher("proto_val")
    r = await client.post(
        f"{API}/assignments", headers=headers, json={"title": "", "questions_per_submission": 11}
    )
    assert r.status_code == 422
    errors = {e["loc"]: e for e in r.json()["errors"]}
    assert errors["body.title"]["type"] == "string_too_short"
    assert (
        errors["body.questions_per_submission"]["ctx"] == {"le": 10}
        or errors["body.questions_per_submission"]["type"] == "less_than_equal"
    )

    a = (await client.post(f"{API}/assignments", headers=headers, json={"title": "A"})).json()
    r = await client.post(
        f"{API}/assignments/{a['id']}/criteria",
        headers=headers,
        json={"name": "x", "max_points": 0},
    )
    assert r.status_code == 422
    assert r.json()["errors"][0]["loc"] == "body.max_points"


@pytest.mark.asyncio
async def test_duplicate_titles_questions_criteria_are_conflicts(client, setup):
    ctx = await setup("proto_dup", questions=1, criteria=(("A", 1),))
    aid, h = ctx["assignment"]["id"], ctx["headers"]
    r = await client.post(
        f"{API}/assignments", headers=h, json={"title": ctx["assignment"]["title"]}
    )
    assert (r.status_code, r.json()["code"]) == (409, "assignment_title_taken")
    r = await client.post(
        f"{API}/assignments/{aid}/questions", headers=h, json={"text": "Вопрос 0"}
    )
    assert (r.status_code, r.json()["code"]) == (409, "question_duplicate")
    r = await client.post(
        f"{API}/assignments/{aid}/criteria", headers=h, json={"name": "A", "max_points": 2}
    )
    assert (r.status_code, r.json()["code"]) == (409, "criterion_duplicate")


@pytest.mark.asyncio
async def test_other_teacher_gets_404_everywhere(client, setup, make_teacher):
    ctx = await setup("proto_own", students=1)
    sub = await call_next(client, ctx)
    other = await make_teacher("proto_intruder")
    aid = ctx["assignment"]["id"]
    qid = (await client.get(f"{API}/assignments/{aid}/questions", headers=ctx["headers"])).json()[
        0
    ]["id"]
    cid = ctx["criteria"][0]["id"]
    urls = [
        ("get", f"/assignments/{aid}"),
        ("patch", f"/assignments/{aid}"),
        ("delete", f"/assignments/{aid}"),
        ("get", f"/assignments/{aid}/questions"),
        ("post", f"/assignments/{aid}/questions"),
        ("patch", f"/assignments/{aid}/questions/{qid}"),
        ("delete", f"/assignments/{aid}/questions/{qid}"),
        ("get", f"/assignments/{aid}/criteria"),
        ("patch", f"/assignments/{aid}/criteria/{cid}"),
        ("get", f"/sessions/{ctx['code']}/submissions"),
        ("get", f"/sessions/{ctx['code']}/submissions/current"),
        ("get", f"/submissions/{sub['id']}"),
        ("put", f"/submissions/{sub['id']}/grades"),
        ("post", f"/submissions/{sub['id']}/complete"),
        ("post", f"/submissions/{sub['id']}/correct"),
        ("get", f"/submissions/{sub['id']}/audit"),
    ]
    bodies = {
        "post": {"text": "x", "name": "x", "max_points": 1, "reason": "because", "scores": []},
        "patch": {"title": "x"},
        "put": {"scores": []},
    }
    for method, url in urls:
        r = await client.request(method, API + url, headers=other, json=bodies.get(method))
        assert r.status_code == 404, (method, url, r.status_code, r.text)
    r = await client.put(
        f"{API}/sessions/{ctx['code']}/assignment", headers=other, json={"assignment_id": None}
    )
    assert r.status_code == 404
    mine = await make_teacher("proto_mine2")
    c = (await client.post(f"{API}/sessions", headers=mine, json={"title": "x"})).json()["code"]
    r = await client.put(
        f"{API}/sessions/{c}/assignment", headers=mine, json={"assignment_id": aid}
    )
    assert r.status_code == 404
    assert (await client.get(f"{API}/assignments", headers=other)).json() == []


@pytest.mark.asyncio
async def test_call_next_picks_distinct_questions_and_records_seed(client, setup):
    ctx = await setup("proto_pick", questions=8, per_submission=3)
    sub = await call_next(client, ctx)
    texts = [q["text"] for q in sub["questions"]]
    assert len(texts) == 3 and len(set(texts)) == 3
    assert sub["questions_short"] is False
    pool = sub["pool_question_ids"]
    assert len(pool) == 8
    assert len(sub["selection_seed"]) == 32
    again = choose_question_ids(sub["selection_seed"], pool, 3)
    qs = (
        await client.get(
            f"{API}/assignments/{ctx['assignment']['id']}/questions", headers=ctx["headers"]
        )
    ).json()
    by_id = {q["id"]: q["text"] for q in qs}
    assert [by_id[i] for i in again] == texts


def test_choose_question_ids_is_deterministic_and_bounded():
    pool = [str(i) for i in range(10)]
    assert choose_question_ids("abc", pool, 4) == choose_question_ids("abc", pool, 4)
    assert choose_question_ids("abc", pool, 4) != choose_question_ids("abd", pool, 4)
    assert len(set(choose_question_ids("x", pool, 99))) == 10
    assert choose_question_ids("x", [], 3) == []


@pytest.mark.asyncio
async def test_small_bank_marks_submission_short(client, setup):
    ctx = await setup("proto_short", questions=2, per_submission=3)
    sub = await call_next(client, ctx)
    assert len(sub["questions"]) == 2
    assert sub["questions_short"] is True


@pytest.mark.asyncio
async def test_complete_flow_scores_total_audit_and_entry_done(client, setup):
    ctx = await setup("proto_flow")
    sub = await call_next(client, ctx)
    h = ctx["headers"]

    r = await client.post(f"{API}/submissions/{sub['id']}/complete", headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "grades_incomplete")
    assert "Код" in r.json()["detail"]

    bad = full_scores(ctx)
    bad["scores"][0]["points"] = 6
    r = await client.post(f"{API}/submissions/{sub['id']}/complete", headers=h, json=bad)
    assert (r.status_code, r.json()["code"]) == (422, "points_out_of_range")

    r = await client.put(
        f"{API}/submissions/{sub['id']}/grades", headers=h, json=full_scores(ctx, 3)
    )
    assert r.status_code == 200

    entry_id = sub["entry_id"]
    r = await client.post(f"{API}/sessions/{ctx['code']}/entries/{entry_id}/done", headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "submission_open")

    r = await client.post(f"{API}/submissions/{sub['id']}/complete", headers=h)
    assert r.status_code == 200, r.text
    done = r.json()
    assert (done["status"], done["total_points"], done["max_points"]) == ("completed", 6, 10)
    assert done["duration_seconds"] is not None and done["ended_at"]

    queue = (await client.get(f"{API}/sessions/{ctx['code']}/queue", headers=h)).json()
    assert all(e["status"] != "called" for e in queue["entries"])

    audit = (await client.get(f"{API}/submissions/{sub['id']}/audit", headers=h)).json()
    assert {a["action"] for a in audit} == {"complete"}
    assert len(audit) == 3

    r = await client.post(f"{API}/submissions/{sub['id']}/complete", headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "already_completed")


@pytest.mark.asyncio
async def test_correction_needs_reason_and_writes_old_and_new(client, setup):
    ctx = await setup("proto_corr")
    sub = await call_next(client, ctx)
    h = ctx["headers"]
    await client.post(
        f"{API}/submissions/{sub['id']}/complete", headers=h, json=full_scores(ctx, 2)
    )
    cid = ctx["criteria"][0]["id"]

    r = await client.post(
        f"{API}/submissions/{sub['id']}/correct",
        headers=h,
        json={"scores": [{"criterion_id": cid, "points": 4}]},
    )
    assert r.status_code == 422
    assert r.json()["errors"][0]["loc"] == "body.reason"

    r = await client.post(
        f"{API}/submissions/{sub['id']}/correct",
        headers=h,
        json={"scores": [{"criterion_id": cid, "points": 4}], "reason": "Пересмотр защиты"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["total_points"] == 6

    audit = (await client.get(f"{API}/submissions/{sub['id']}/audit", headers=h)).json()
    last = audit[-1]
    assert (last["action"], last["field"], last["old_value"], last["new_value"]) == (
        "correct",
        f"score:{cid}",
        "2",
        "4",
    )
    assert last["reason"] == "Пересмотр защиты"

    r = await client.post(
        f"{API}/submissions/{sub['id']}/correct",
        headers=h,
        json={"scores": [{"criterion_id": cid, "points": 4}], "reason": "Ещё раз"},
    )
    assert (r.status_code, r.json()["code"]) == (409, "nothing_to_correct")


@pytest.mark.asyncio
async def test_correct_before_completion_is_refused(client, setup):
    ctx = await setup("proto_early")
    sub = await call_next(client, ctx)
    r = await client.post(
        f"{API}/submissions/{sub['id']}/correct",
        headers=ctx["headers"],
        json={"scores": [], "comment": "x", "reason": "причина"},
    )
    assert (r.status_code, r.json()["code"]) == (409, "not_completed")


@pytest.mark.asyncio
async def test_student_sees_only_own_breakdown_after_completion(client, setup):
    ctx = await setup("proto_stud", students=2)
    first, second = ctx["students"]
    sub = await call_next(client, ctx)
    mine = f"{API}/sessions/{ctx['code']}/me/submission"

    r = await client.get(mine, headers={"X-Entry-Token": first["token"]})
    assert r.status_code == 200 and r.json() == {
        "status": "in_progress",
        "attempt_number": 1,
        "questions": [],
        "scores": [],
        "total_points": None,
        "max_points": None,
        "comment": "",
    }
    r = await client.get(mine, headers={"X-Entry-Token": second["token"]})
    assert (r.status_code, r.json()["code"]) == (404, "submission_not_found")

    await client.post(
        f"{API}/submissions/{sub['id']}/complete", headers=ctx["headers"], json=full_scores(ctx, 4)
    )
    r = await client.get(mine, headers={"X-Entry-Token": first["token"]})
    body = r.json()
    assert body["status"] == "completed" and body["total_points"] == 8
    assert len(body["questions"]) == 3 and len(body["scores"]) == 2
    assert "selection_seed" not in body and "pool_question_ids" not in body

    r = await client.get(mine, headers={"X-Entry-Token": second["token"]})
    assert r.status_code == 404
    r = await client.get(mine, headers={"X-Entry-Token": "z" * 20})
    assert (r.status_code, r.json()["code"]) == (404, "entry_not_found")
    assert (await client.get(mine)).status_code == 401


@pytest.mark.asyncio
async def test_skip_cancels_and_recall_keeps_the_same_questions(client, setup):
    ctx = await setup("proto_skip")
    h = ctx["headers"]
    sub = await call_next(client, ctx)
    r = await client.post(f"{API}/sessions/{ctx['code']}/entries/{sub['entry_id']}/skip", headers=h)
    assert r.status_code == 200
    r = await client.get(f"{API}/submissions/{sub['id']}", headers=h)
    assert r.json()["status"] == "cancelled"

    r = await client.post(
        f"{API}/sessions/{ctx['code']}/entries/{sub['entry_id']}/requeue", headers=h
    )
    assert r.status_code == 200
    again = await call_next(client, ctx)
    assert again["id"] == sub["id"]
    assert again["status"] == "in_progress"
    assert [q["text"] for q in again["questions"]] == [q["text"] for q in sub["questions"]]
    assert again["selection_seed"] == sub["selection_seed"]
    audit = (await client.get(f"{API}/submissions/{sub['id']}/audit", headers=h)).json()
    assert [a["action"] for a in audit] == ["reopen"]


@pytest.mark.asyncio
async def test_criteria_are_locked_after_first_submission_and_snapshots_survive(client, setup):
    ctx = await setup("proto_lock")
    h, aid = ctx["headers"], ctx["assignment"]["id"]
    sub = await call_next(client, ctx)
    cid = ctx["criteria"][0]["id"]

    for method, url, body in [
        ("post", f"/assignments/{aid}/criteria", {"name": "Новый", "max_points": 3}),
        ("patch", f"/assignments/{aid}/criteria/{cid}", {"max_points": 9}),
        ("delete", f"/assignments/{aid}/criteria/{cid}", None),
    ]:
        r = await client.request(method, API + url, headers=h, json=body)
        assert (r.status_code, r.json()["code"]) == (409, "criteria_locked"), (method, r.text)

    r = await client.delete(f"{API}/assignments/{aid}", headers=h)
    assert (r.status_code, r.json()["code"]) == (409, "assignment_has_submissions")

    before = sub["questions"][0]["text"]
    qs = (await client.get(f"{API}/assignments/{aid}/questions", headers=h)).json()
    chosen = next(q for q in qs if q["text"] == before)
    r = await client.patch(
        f"{API}/assignments/{aid}/questions/{chosen['id']}", headers=h, json={"text": "Изменено"}
    )
    assert r.status_code == 200
    r = await client.delete(f"{API}/assignments/{aid}/questions/{chosen['id']}", headers=h)
    assert r.status_code == 204
    after = (await client.get(f"{API}/submissions/{sub['id']}", headers=h)).json()
    assert after["questions"][0]["text"] == before

    assert (await client.get(f"{API}/assignments/{aid}", headers=h)).json()[
        "criteria_locked"
    ] is True


@pytest.mark.asyncio
async def test_session_assignment_cannot_change_after_submissions(client, setup):
    ctx = await setup("proto_swap")
    await call_next(client, ctx)
    r = await client.put(
        f"{API}/sessions/{ctx['code']}/assignment",
        headers=ctx["headers"],
        json={"assignment_id": None},
    )
    assert (r.status_code, r.json()["code"]) == (409, "session_has_submissions")


@pytest.mark.asyncio
async def test_sessions_without_assignment_keep_old_behaviour(
    client, make_teacher, make_session, join
):
    h = await make_teacher("proto_plain")
    code = await make_session(h)
    s = await join(code, "Plain")
    r = await client.post(f"{API}/sessions/{code}/next", headers=h)
    assert r.status_code == 200
    r = await client.post(f"{API}/sessions/{code}/entries/{r.json()['id']}/done", headers=h)
    assert r.status_code == 200 and r.json()["status"] == "done"
    r = await client.get(
        f"{API}/sessions/{code}/me/submission", headers={"X-Entry-Token": s["token"]}
    )
    assert (r.status_code, r.json()["code"]) == (404, "submission_not_found")


@pytest.mark.asyncio
async def test_audit_log_rejects_update_and_delete(client, setup, db_session):
    ctx = await setup("proto_immutable")
    sub = await call_next(client, ctx)
    await client.post(
        f"{API}/submissions/{sub['id']}/complete", headers=ctx["headers"], json=full_scores(ctx)
    )

    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("UPDATE audit_log SET new_value = '99'"))
    await db_session.rollback()
    with pytest.raises(DBAPIError, match="append-only"):
        await db_session.execute(text("DELETE FROM audit_log"))
    await db_session.rollback()
    count = (
        await db_session.execute(
            text("SELECT count(*) FROM audit_log WHERE submission_id = :s"), {"s": sub["id"]}
        )
    ).scalar_one()
    assert count == 3


@pytest.mark.asyncio
async def test_audit_reason_is_required_by_the_database(db_session):
    with pytest.raises(DBAPIError):
        await db_session.execute(
            text(
                "INSERT INTO audit_log (id, occurred_at, submission_id, actor_id, action, field, reason)"
                " VALUES ('x', now(), 's', 't', 'correct', 'comment', '  ')"
            )
        )
    await db_session.rollback()


@pytest.mark.asyncio
async def test_concurrent_completes_finish_exactly_once(client, setup):
    ctx = await setup("proto_race_complete")
    sub = await call_next(client, ctx)
    h = ctx["headers"]
    responses = await asyncio.gather(
        *(
            client.post(
                f"{API}/submissions/{sub['id']}/complete", headers=h, json=full_scores(ctx, 3)
            )
            for _ in range(8)
        )
    )
    assert sorted(r.status_code for r in responses) == [200] + [409] * 7
    audit = (await client.get(f"{API}/submissions/{sub['id']}/audit", headers=h)).json()
    assert len(audit) == 3


@pytest.mark.asyncio
async def test_concurrent_corrections_keep_an_unbroken_chain(client, setup):
    ctx = await setup("proto_race_corr", criteria=(("Код", 100),))
    sub = await call_next(client, ctx)
    h = ctx["headers"]
    cid = ctx["criteria"][0]["id"]
    await client.post(
        f"{API}/submissions/{sub['id']}/complete", headers=h, json=full_scores(ctx, 1)
    )

    responses = await asyncio.gather(
        *(
            client.post(
                f"{API}/submissions/{sub['id']}/correct",
                headers=h,
                json={"scores": [{"criterion_id": cid, "points": 10 + i}], "reason": f"правка {i}"},
            )
            for i in range(10)
        )
    )
    assert {r.status_code for r in responses} == {200}
    audit = (await client.get(f"{API}/submissions/{sub['id']}/audit", headers=h)).json()
    chain = [a for a in audit if a["action"] == "correct"]
    assert len(chain) == 10
    previous = "1"
    for entry in chain:
        assert entry["old_value"] == previous, chain
        previous = entry["new_value"]
    final = (await client.get(f"{API}/submissions/{sub['id']}", headers=h)).json()
    assert str(final["total_points"]) == previous


@pytest.mark.asyncio
async def test_concurrent_next_creates_one_submission(client, setup):
    ctx = await setup("proto_race_next", students=3)
    responses = await asyncio.gather(
        *(
            client.post(f"{API}/sessions/{ctx['code']}/next", headers=ctx["headers"])
            for _ in range(5)
        )
    )
    assert sorted(r.status_code for r in responses) == [200] + [409] * 4
    subs = (
        await client.get(f"{API}/sessions/{ctx['code']}/submissions", headers=ctx["headers"])
    ).json()
    assert len(subs) == 1


async def bare_assignment(client, headers, title):
    a = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": title, "questions_per_submission": 3},
        )
    ).json()
    for i in range(4):
        await client.post(
            f"{API}/assignments/{a['id']}/questions",
            headers=headers,
            json={"text": f"Вопрос {i}", "difficulty": 1, "topic": "t"},
        )
    crit = (
        await client.post(
            f"{API}/assignments/{a['id']}/criteria",
            headers=headers,
            json={"name": "Код", "max_points": 5},
        )
    ).json()
    return a, crit


@pytest.mark.asyncio
async def test_work_attached_after_a_student_was_called_still_opens_a_submission(
    client, make_teacher, make_session, join
):
    headers = await make_teacher("proto_late_attach")
    a, crit = await bare_assignment(client, headers, "Позднее подключение")
    code = await make_session(headers)
    student = await join(code, "Поздний", token="late-student-token-1")
    called = (await client.post(f"{API}/sessions/{code}/next", headers=headers)).json()
    r = await client.put(
        f"{API}/sessions/{code}/assignment", headers=headers, json={"assignment_id": a["id"]}
    )
    assert r.status_code == 204, r.text
    current = await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
    assert current.status_code == 200 and len(current.json()["questions"]) == 3
    done = await client.post(f"{API}/sessions/{code}/entries/{called['id']}/done", headers=headers)
    assert done.status_code == 409 and done.json()["code"] == "submission_open"
    r = await client.post(
        f"{API}/submissions/{current.json()['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": crit["id"], "points": 4}]},
    )
    assert r.status_code == 200
    mine = await client.get(
        f"{API}/sessions/{code}/me/submission", headers={"X-Entry-Token": student["token"]}
    )
    assert mine.status_code == 200 and mine.json()["total_points"] == 4


@pytest.mark.asyncio
async def test_closing_a_session_cancels_the_open_submission(setup, client):
    ctx = await setup("proto_close_open")
    await client.post(f"{API}/sessions/{ctx['code']}/next", headers=ctx["headers"])
    r = await client.post(f"{API}/sessions/{ctx['code']}/close", headers=ctx["headers"])
    assert r.status_code == 200
    subs = (
        await client.get(f"{API}/sessions/{ctx['code']}/submissions", headers=ctx["headers"])
    ).json()
    assert [s["status"] for s in subs] == ["cancelled"]


@pytest.mark.asyncio
async def test_idle_auto_close_cancels_the_open_submission(setup, client, db_session):
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    from models.queue_session import QueueSessionORM
    from services.maintenance import close_idle_sessions
    from tests.conftest import TestingSessionLocal

    ctx = await setup("proto_idle_open")
    await client.post(f"{API}/sessions/{ctx['code']}/next", headers=ctx["headers"])
    await db_session.execute(
        update(QueueSessionORM)
        .where(QueueSessionORM.code == ctx["code"])
        .values(created_at=datetime.now(UTC) - timedelta(hours=9))
    )
    from models.entry import EntryORM

    await db_session.execute(
        update(EntryORM)
        .where(EntryORM.session_id == QueueSessionORM.id, QueueSessionORM.code == ctx["code"])
        .values(
            joined_at=datetime.now(UTC) - timedelta(hours=9),
            called_at=datetime.now(UTC) - timedelta(hours=9),
        )
    )
    await db_session.commit()
    assert ctx["code"] in await close_idle_sessions(TestingSessionLocal, hours=6)
    subs = (
        await client.get(f"{API}/sessions/{ctx['code']}/submissions", headers=ctx["headers"])
    ).json()
    assert [s["status"] for s in subs] == ["cancelled"]
