import asyncio

import pytest
from sqlalchemy import text

API = "/api/v1"
S = f"{API}/sessions"


@pytest.fixture
def room(client, make_teacher):
    async def _room(username: str, **extra):
        headers = await make_teacher(username)
        response = await client.post(S, headers=headers, json={"title": "Сдача", **extra})
        assert response.status_code == 201, response.text
        return headers, response.json()

    return _room


@pytest.fixture
def enter(client):
    async def _enter(code: str, name: str) -> dict:
        token = name.encode().hex().ljust(16, "x")
        response = await client.post(f"{S}/{code}/join", json={"name": name, "token": token})
        assert response.status_code == 200, response.text
        return {**response.json(), "token": token}

    return _enter


async def finish_quick(client, headers, code, points, comment=""):
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    criteria = (
        await client.get(
            f"{API}/assignments/{(await session_of(client, headers, code))['assignment_id']}/criteria",
            headers=headers,
        )
    ).json()
    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={
            "scores": [{"criterion_id": criteria[0]["id"], "points": points}],
            "comment": comment,
        },
    )
    return current, done


async def session_of(client, headers, code):
    return next(s for s in (await client.get(S, headers=headers)).json() if s["code"] == code)


@pytest.mark.asyncio
async def test_default_mode_is_the_full_protocol_as_before(client, room):
    headers, session = await room("sm_default")
    assert (session["session_mode"], session["quick_max_points"]) == ("protocol", 10)
    assert session["assignment_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "extra",
    [{"session_mode": "other"}, {"quick_max_points": 0}, {"quick_max_points": 101}],
)
async def test_bad_mode_settings_are_refused(client, make_teacher, extra):
    headers = await make_teacher(f"sm_bad_{abs(hash(str(extra)))}")
    response = await client.post(S, headers=headers, json={"title": "X", **extra})
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_queue_only_session_serves_students_without_any_grades(client, room, enter):
    headers, session = await room("sm_queue", session_mode="queue_only")
    code = session["code"]
    await enter(code, "Анна")
    await enter(code, "Борис")
    called = await client.post(f"{S}/{code}/next", headers=headers)
    assert called.status_code == 200
    assert (await client.get(f"{S}/{code}/submissions/current", headers=headers)).status_code == 404
    done = await client.post(f"{S}/{code}/entries/{called.json()['id']}/done", headers=headers)
    assert done.status_code == 200 and done.json()["status"] == "done"
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    assert (queue["session_mode"], queue["assignment_id"]) == ("queue_only", None)


@pytest.mark.asyncio
async def test_queue_only_and_quick_sessions_do_not_take_a_protocol_assignment(client, room):
    headers, queue_only = await room("sm_noasg", session_mode="queue_only")
    assignment = (
        await client.post(f"{API}/assignments", headers=headers, json={"title": "Работа"})
    ).json()
    refused = await client.put(
        f"{S}/{queue_only['code']}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    assert refused.status_code == 409 and refused.json()["code"] == "mode_no_assignment"
    quick = (
        await client.post(S, headers=headers, json={"title": "Быстро", "session_mode": "quick"})
    ).json()
    refused = await client.put(
        f"{S}/{quick['code']}/assignment", headers=headers, json={"assignment_id": None}
    )
    assert refused.status_code == 409


@pytest.mark.asyncio
async def test_quick_mode_creates_one_hidden_criterion_with_the_chosen_maximum(client, room):
    headers, session = await room("sm_quick", session_mode="quick", quick_max_points=20)
    assert session["assignment_id"] is not None and session["quick_max_points"] == 20
    criteria = (
        await client.get(f"{API}/assignments/{session['assignment_id']}/criteria", headers=headers)
    ).json()
    assert [(c["name"], c["max_points"]) for c in criteria] == [("Оценка", 20)]
    assert (await client.get(f"{API}/assignments", headers=headers)).json() == []
    one = (
        await client.get(f"{API}/assignments/{session['assignment_id']}", headers=headers)
    ).json()
    assert one["title"] == "Быстрая оценка (0–20)"


@pytest.mark.asyncio
async def test_sessions_with_the_same_maximum_share_one_hidden_assignment(client, room):
    headers, first = await room("sm_share", session_mode="quick")
    second = (
        await client.post(S, headers=headers, json={"title": "Ещё", "session_mode": "quick"})
    ).json()
    third = (
        await client.post(
            S,
            headers=headers,
            json={"title": "Другой максимум", "session_mode": "quick", "quick_max_points": 5},
        )
    ).json()
    assert first["assignment_id"] == second["assignment_id"] != third["assignment_id"]


@pytest.mark.asyncio
async def test_a_user_assignment_with_the_same_title_does_not_break_quick_mode(client, room):
    headers, _ = await room("sm_title")
    await client.post(
        f"{API}/assignments", headers=headers, json={"title": "Быстрая оценка (0–10)"}
    )
    response = await client.post(
        S, headers=headers, json={"title": "Быстро", "session_mode": "quick"}
    )
    assert response.status_code == 201
    titles = [a["title"] for a in (await client.get(f"{API}/assignments", headers=headers)).json()]
    assert titles == ["Быстрая оценка (0–10)"]


@pytest.mark.asyncio
async def test_a_quick_grade_is_stored_like_any_other_submission(client, room, enter):
    headers, session = await room("sm_grade", session_mode="quick", quick_max_points=10)
    code = session["code"]
    anna = await enter(code, "Анна")
    current, done = await finish_quick(client, headers, code, 8, "Хорошо отвечал")
    assert done.status_code == 200
    body = done.json()
    assert (body["total_points"], body["max_points"], body["comment"]) == (8, 10, "Хорошо отвечал")
    assert body["questions"] == []
    mine = (
        await client.get(f"{S}/{code}/me/submission", headers={"X-Entry-Token": anna["token"]})
    ).json()
    assert (mine["total_points"], mine["max_points"]) == (8, 10)
    audit = (await client.get(f"{API}/submissions/{current['id']}/audit", headers=headers)).json()
    assert [a["new_value"] for a in audit if a["field"].startswith("score:")] == ["8"]
    over = await finish_quick_over(client, headers, code, enter)
    assert over.status_code == 422 and over.json()["code"] == "points_out_of_range"


async def finish_quick_over(client, headers, code, enter):
    await enter(code, "Борис")
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    criteria = (
        await client.get(
            f"{API}/assignments/{(await session_of(client, headers, code))['assignment_id']}/criteria",
            headers=headers,
        )
    ).json()
    return await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criteria[0]["id"], "points": 11}], "comment": ""},
    )


@pytest.mark.asyncio
async def test_history_analytics_and_the_student_card_work_with_quick_grades(client, room, enter):
    headers, session = await room("sm_hist", group="ИС-22", session_mode="quick")
    code = session["code"]
    for i, points in enumerate((10, 8, 6, 4, 2)):
        await enter(code, f"Студент {i}")
        await finish_quick(client, headers, code, points)
    students = (await client.get(f"{API}/students", headers=headers)).json()
    assert len(students) == 5
    card = (await client.get(f"{API}/students/{students[0]['id']}", headers=headers)).json()
    attempt = card["assignments"][0]["attempts"][0]
    assert card["assignments"][0]["title"] == "Быстрая оценка (0–10)"
    assert attempt["max_points"] == 10 and attempt["scores"][0]["name"] == "Оценка"
    analytics = (await client.get(f"{API}/sessions/{code}/analytics", headers=headers)).json()
    assert analytics["submissions"] == 5 and analytics["sufficient"] is True
    weak = analytics["weak_spots"]
    assert weak["sufficient"] is True and weak["criteria"][0]["name"] == "Оценка"


@pytest.mark.asyncio
async def test_a_retake_across_quick_sessions_is_attempt_two(client, room, enter):
    headers, first = await room("sm_retake", group="ИС-22", session_mode="quick")
    await enter(first["code"], "Анна Иванова")
    await finish_quick(client, headers, first["code"], 5)
    second = (
        await client.post(
            S,
            headers=headers,
            json={"title": "Пересдача", "group": "ИС-22", "session_mode": "quick"},
        )
    ).json()
    await enter(second["code"], "Анна Иванова")
    _, done = await finish_quick(client, headers, second["code"], 9)
    assert done.json()["attempt_number"] == 2


@pytest.mark.asyncio
async def test_the_hidden_assignment_cannot_be_edited_copied_or_published(
    client, room, make_teacher
):
    headers, session = await room("sm_system", session_mode="quick")
    aid = session["assignment_id"]
    base = f"{API}/assignments/{aid}"
    cid = (await client.get(f"{base}/criteria", headers=headers)).json()[0]["id"]
    refusals = [
        await client.patch(base, headers=headers, json={"title": "Моя"}),
        await client.delete(base, headers=headers),
        await client.post(f"{base}/questions", headers=headers, json={"text": "Вопрос?"}),
        await client.post(
            f"{base}/criteria", headers=headers, json={"name": "Ещё", "max_points": 5}
        ),
        await client.patch(f"{base}/criteria/{cid}", headers=headers, json={"max_points": 99}),
        await client.delete(f"{base}/criteria/{cid}", headers=headers),
    ]
    assert [r.status_code for r in refusals] == [409] * 6
    assert {r.json()["code"] for r in refusals} == {"system_assignment"}
    assert "служебная" in refusals[0].json()["detail"]
    copy = await client.post(f"{base}/copy", headers=headers, json={})
    assert copy.status_code == 404
    space = (await client.post(f"{API}/spaces", headers=headers, json={"name": "Группа"})).json()
    publish = await client.post(
        f"{API}/spaces/{space['id']}/publications", headers=headers, json={"assignment_id": aid}
    )
    assert publish.status_code == 404
    other = await make_teacher("sm_system_other")
    assert (await client.get(base, headers=other)).status_code == 404


@pytest.mark.asyncio
async def test_hidden_assignments_do_not_use_up_the_assignment_limit(client, room, monkeypatch):
    from core.config import settings

    monkeypatch.setattr(settings, "MAX_ASSIGNMENTS_PER_TEACHER", 1)
    headers, _ = await room("sm_limit", session_mode="quick")
    first = await client.post(f"{API}/assignments", headers=headers, json={"title": "Моя"})
    assert first.status_code == 201
    second = await client.post(f"{API}/assignments", headers=headers, json={"title": "Ещё"})
    assert second.status_code == 409 and second.json()["code"] == "assignment_limit"


@pytest.mark.asyncio
async def test_concurrent_quick_sessions_create_one_hidden_assignment(client, room, db_session):
    headers, _ = await room("sm_race")
    results = await asyncio.gather(
        *(
            client.post(S, headers=headers, json={"title": f"Быстро {i}", "session_mode": "quick"})
            for i in range(8)
        )
    )
    assert [r.status_code for r in results] == [201] * 8
    assert len({r.json()["assignment_id"] for r in results}) == 1
    one = results[0].json()["assignment_id"]
    count = (
        await db_session.execute(
            text(
                "SELECT count(*) FROM assignments WHERE is_system AND teacher_id ="
                " (SELECT teacher_id FROM assignments WHERE id = :id)"
            ),
            {"id": one},
        )
    ).scalar_one()
    assert count == 1


@pytest.mark.asyncio
async def test_teachers_do_not_see_each_others_quick_data(client, room, make_teacher):
    headers, session = await room("sm_iso", session_mode="quick")
    other = await make_teacher("sm_iso_other")
    other_session = (
        await client.post(S, headers=other, json={"title": "Чужая", "session_mode": "quick"})
    ).json()
    assert other_session["assignment_id"] != session["assignment_id"]
    assert (
        await client.get(f"{API}/assignments/{session['assignment_id']}/criteria", headers=other)
    ).status_code == 404


@pytest.mark.asyncio
async def test_existing_sessions_keep_working_with_a_protocol_assignment(client, room, enter):
    headers, session = await room("sm_old")
    code = session["code"]
    assignment = (
        await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": "Работа", "questions_per_submission": 1},
        )
    ).json()
    await client.post(
        f"{API}/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос?"},
    )
    await client.post(
        f"{API}/assignments/{assignment['id']}/criteria",
        headers=headers,
        json={"name": "Код", "max_points": 5},
    )
    attached = await client.put(
        f"{S}/{code}/assignment", headers=headers, json={"assignment_id": assignment["id"]}
    )
    assert attached.status_code == 204
    await enter(code, "Анна")
    await client.post(f"{S}/{code}/next", headers=headers)
    current = (await client.get(f"{S}/{code}/submissions/current", headers=headers)).json()
    assert len(current["questions"]) == 1
    queue = (await client.get(f"{S}/{code}/queue", headers=headers)).json()
    assert queue["session_mode"] == "protocol" and queue["assignment_id"] == assignment["id"]


@pytest.mark.asyncio
async def test_public_info_tells_whether_the_session_has_grades(client, room):
    headers, plain = await room("sm_graded_plain", session_mode="queue_only")
    _, quick = await room("sm_graded_quick", session_mode="quick")
    _, protocol = await room("sm_graded_protocol")
    assert (await client.get(f"{S}/{plain['code']}")).json()["graded"] is False
    assert (await client.get(f"{S}/{quick['code']}")).json()["graded"] is True
    assert (await client.get(f"{S}/{protocol['code']}")).json()["graded"] is False
