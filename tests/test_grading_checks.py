import uuid

import pytest

from core.config import settings
from services.grading_checks import check_deviation, deviation_text, is_extreme, reason_given

API = "/api/v1"


def test_only_zero_and_the_maximum_are_extreme():
    assert is_extreme(0, 5) and is_extreme(5, 5)
    assert not is_extreme(1, 5) and not is_extreme(4, 5)


def test_a_reason_needs_the_minimum_length_after_trimming():
    assert not reason_given("    ab ")
    assert reason_given("  abcde ")


def test_the_warning_needs_the_minimum_sample():
    history = [3, 3, 4, 3]

    assert check_deviation(0, history, 2.0) is None
    assert check_deviation(0, [*history, 3], 2.0) is not None


def test_the_threshold_is_strict_and_measured_in_standard_deviations():
    history = [2, 4, 2, 4, 2, 4]
    spread = 1.0954451150103321

    inside = check_deviation(3 + 2 * spread - 0.0001, history, 2.0)
    outside = check_deviation(3 + 2 * spread + 0.0001, history, 2.0)

    assert inside is not None and not inside.warn
    assert outside is not None and outside.warn
    assert outside.samples == 6 and outside.mean == 3


def test_a_smaller_threshold_warns_sooner():
    history = [2, 4, 2, 4, 2, 4]

    assert not check_deviation(5, history, 2.0).warn
    assert check_deviation(5, history, 1.0).warn


def test_identical_history_warns_on_any_difference_and_stays_quiet_on_a_match():
    history = [3, 3, 3, 3, 3]

    same = check_deviation(3, history, 2.0)
    other = check_deviation(2, history, 2.0)

    assert same is not None and not same.warn
    assert other is not None and other.warn and other.distance is None


def test_the_text_names_the_usual_value_and_the_current_one():
    assert deviation_text(3.04, 0) == (
        "Ваши оценки по этому критерию обычно около 3,0, сейчас 0. Проверьте."
    )


@pytest.fixture
def lesson(client, make_teacher, make_session, join):
    async def _lesson(prefix: str, *, max_points: int = 5, **settings_body):
        headers = await make_teacher(f"{prefix}_{uuid.uuid4().hex[:6]}")
        code = await make_session(headers)
        made = await client.post(
            f"{API}/assignments",
            headers=headers,
            json={"title": f"Работа {prefix}", "questions_per_submission": 1, **settings_body},
        )
        assert made.status_code == 201, made.text
        assignment = made.json()
        await client.post(
            f"{API}/assignments/{assignment['id']}/questions",
            headers=headers,
            json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
        )
        criterion = (
            await client.post(
                f"{API}/assignments/{assignment['id']}/criteria",
                headers=headers,
                json={"name": "Ответ", "max_points": max_points},
            )
        ).json()
        await client.put(
            f"{API}/sessions/{code}/assignment",
            headers=headers,
            json={"assignment_id": assignment["id"]},
        )
        return headers, code, assignment, criterion

    return _lesson


@pytest.fixture
def serve(client, join):
    async def _serve(
        headers, code, criterion, points, *, name, comment="", finish=True, token=None
    ):
        entry = await join(code, name, token=token)
        await client.post(f"{API}/sessions/{code}/next", headers=headers)
        current = (
            await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
        ).json()
        body = {"scores": [{"criterion_id": criterion["id"], "points": points, "comment": comment}]}
        if not finish:
            return entry, current, None
        done = await client.post(
            f"{API}/submissions/{current['id']}/complete", headers=headers, json=body
        )
        return entry, current, done

    return _serve


async def history(serve, headers, code, criterion, values):
    for i, points in enumerate(values):
        _, _, done = await serve(headers, code, criterion, points, name=f"Прежний {i}")
        assert done.status_code == 200, done.text


async def check(client, headers, submission, criterion, points):
    return await client.post(
        f"{API}/submissions/{submission['id']}/deviation-check",
        headers=headers,
        json={"criterion_id": criterion["id"], "points": points},
    )


async def events(client, headers, code):
    return (await client.get(f"{API}/sessions/{code}/events-log", headers=headers)).json()


@pytest.mark.asyncio
async def test_both_features_are_off_by_default_and_change_nothing(client, lesson, serve):
    headers, code, assignment, criterion = await lesson("gc_default")

    shown = (await client.get(f"{API}/assignments/{assignment['id']}", headers=headers)).json()
    _, _, done = await serve(headers, code, criterion, 0, name="Студент Первый")

    assert shown["require_extreme_reason"] is False
    assert shown["deviation_warning"] is False
    assert shown["deviation_sigma"] == 2.0
    assert done.status_code == 200


@pytest.mark.asyncio
async def test_settings_can_be_changed_and_are_validated(client, lesson):
    headers, _, assignment, _ = await lesson("gc_settings")
    url = f"{API}/assignments/{assignment['id']}"

    ok = await client.patch(
        url,
        headers=headers,
        json={"require_extreme_reason": True, "deviation_warning": True, "deviation_sigma": 1.5},
    )
    low = await client.patch(url, headers=headers, json={"deviation_sigma": 0.1})
    high = await client.patch(url, headers=headers, json={"deviation_sigma": 9})

    assert ok.status_code == 200 and ok.json()["deviation_sigma"] == 1.5
    assert ok.json()["require_extreme_reason"] is True
    assert low.status_code == 422 and high.status_code == 422


@pytest.mark.asyncio
async def test_a_copy_keeps_the_settings(client, lesson):
    headers, _, assignment, _ = await lesson(
        "gc_copy", require_extreme_reason=True, deviation_warning=True, deviation_sigma=3
    )

    copy = (
        await client.post(f"{API}/assignments/{assignment['id']}/copy", headers=headers, json={})
    ).json()

    assert copy["require_extreme_reason"] is True
    assert copy["deviation_warning"] is True and copy["deviation_sigma"] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("points", [0, 5])
async def test_an_extreme_score_needs_a_reason_when_required(client, lesson, serve, points):
    headers, code, _, criterion = await lesson("gc_need", require_extreme_reason=True)

    _, current, refused = await serve(headers, code, criterion, points, name="Студент Первый")
    state = (await client.get(f"{API}/submissions/{current['id']}", headers=headers)).json()

    assert refused.status_code == 422 and refused.json()["code"] == "reason_required"
    assert state["status"] == "in_progress"


@pytest.mark.asyncio
async def test_a_short_reason_is_not_enough_and_a_long_enough_one_is(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_len", require_extreme_reason=True)
    minimum = settings.EXTREME_REASON_MIN_LENGTH

    _, current, short = await serve(
        headers, code, criterion, 0, name="Студент Первый", comment="а" * (minimum - 1)
    )
    again = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 0, "comment": "а" * minimum}]},
    )

    assert short.status_code == 422
    assert again.status_code == 200, again.text


@pytest.mark.asyncio
async def test_a_middle_score_never_needs_a_reason(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_mid", require_extreme_reason=True)

    _, _, done = await serve(headers, code, criterion, 3, name="Студент Первый")

    assert done.status_code == 200


@pytest.mark.asyncio
async def test_a_draft_can_be_saved_without_a_reason(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_draft", require_extreme_reason=True)
    _, current, _ = await serve(headers, code, criterion, 0, name="Студент Первый", finish=False)

    saved = await client.put(
        f"{API}/submissions/{current['id']}/grades",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 0, "comment": ""}]},
    )

    assert saved.status_code == 200


@pytest.mark.asyncio
async def test_the_reason_is_shown_to_the_student_next_to_the_score(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_student", require_extreme_reason=True)

    entry, _, done = await serve(
        headers,
        code,
        criterion,
        5,
        name="Студент Первый",
        comment="Полный и точный ответ",
        token="student-token-0001",
    )
    mine = (
        await client.get(
            f"{API}/sessions/{code}/me/submission", headers={"X-Entry-Token": entry["token"]}
        )
    ).json()

    assert done.status_code == 200
    assert mine["scores"][0]["comment"] == "Полный и точный ответ"


@pytest.mark.asyncio
async def test_a_correction_to_an_extreme_score_needs_a_reason_too(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_correct", require_extreme_reason=True)
    _, current, done = await serve(headers, code, criterion, 3, name="Студент Первый")
    assert done.status_code == 200
    url = f"{API}/submissions/{current['id']}/correct"

    bare = await client.post(
        url,
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 5}], "reason": "Пересмотр"},
    )
    explained = await client.post(
        url,
        headers=headers,
        json={
            "scores": [
                {"criterion_id": criterion["id"], "points": 5, "comment": "Нашёл полное решение"}
            ],
            "reason": "Пересмотр",
        },
    )
    middle = await client.post(
        url,
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 4}], "reason": "Ещё раз"},
    )

    assert bare.status_code == 422 and bare.json()["code"] == "reason_required"
    assert explained.status_code == 200, explained.text
    assert middle.status_code == 200, middle.text


@pytest.mark.asyncio
async def test_without_the_setting_extreme_scores_need_no_reason(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_off")

    _, _, zero = await serve(headers, code, criterion, 0, name="Студент Первый")
    _, _, top = await serve(headers, code, criterion, 5, name="Студент Второй")

    assert zero.status_code == 200 and top.status_code == 200


@pytest.mark.asyncio
async def test_the_check_is_silent_when_the_warning_is_off(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_quiet")
    await history(serve, headers, code, criterion, [3, 3, 4, 3, 3, 4])
    _, current, _ = await serve(headers, code, criterion, 0, name="Новый", finish=False)

    answer = await check(client, headers, current, criterion, 0)

    assert answer.status_code == 200
    assert answer.json()["enabled"] is False and answer.json()["warn"] is False
    assert await events(client, headers, code) == []


@pytest.mark.asyncio
async def test_no_warning_below_the_minimum_sample(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_few", deviation_warning=True)
    await history(serve, headers, code, criterion, [3, 3, 4, 3])
    _, current, _ = await serve(headers, code, criterion, 0, name="Новый", finish=False)

    answer = (await check(client, headers, current, criterion, 0)).json()

    assert answer["enabled"] is True and answer["warn"] is False
    assert answer["samples"] == 4 and answer["min_samples"] == settings.DEVIATION_MIN_SAMPLES
    assert await events(client, headers, code) == []


@pytest.mark.asyncio
async def test_a_far_score_warns_with_the_usual_value_in_the_text(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_warn", deviation_warning=True)
    await history(serve, headers, code, criterion, [3, 4, 3, 4, 3, 4])
    _, current, _ = await serve(headers, code, criterion, 0, name="Новый", finish=False)

    answer = (await check(client, headers, current, criterion, 0)).json()
    near = (await check(client, headers, current, criterion, 4)).json()

    assert answer["warn"] is True and answer["mean"] == 3.5
    assert answer["text"] == "Ваши оценки по этому критерию обычно около 3,5, сейчас 0. Проверьте."
    assert near["warn"] is False and near["text"] == ""


@pytest.mark.asyncio
async def test_the_warning_never_blocks_the_teacher(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_free", deviation_warning=True)
    await history(serve, headers, code, criterion, [3, 4, 3, 4, 3, 4])
    _, current, _ = await serve(headers, code, criterion, 0, name="Новый", finish=False)
    await check(client, headers, current, criterion, 0)

    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 0, "comment": ""}]},
    )

    assert done.status_code == 200 and done.json()["total_points"] == 0


@pytest.mark.asyncio
async def test_a_shown_warning_is_logged_without_names_and_a_quiet_check_is_not(
    client, lesson, serve
):
    headers, code, _, criterion = await lesson("gc_log", deviation_warning=True)
    await history(serve, headers, code, criterion, [3, 4, 3, 4, 3, 4])
    _, current, _ = await serve(headers, code, criterion, 0, name="Секретное Имя", finish=False)

    await check(client, headers, current, criterion, 4)
    assert await events(client, headers, code) == []
    await check(client, headers, current, criterion, 0)
    log = await events(client, headers, code)

    assert [(e["kind"], e["details"]) for e in log] == [
        ("deviation_shown", {"criterion_id": criterion["id"]})
    ]
    assert "Секретное Имя" not in str(log) and current["id"] not in str(log)


@pytest.mark.asyncio
async def test_the_sensitivity_follows_the_setting(client, lesson, serve):
    headers, code, assignment, criterion = await lesson("gc_sigma", deviation_warning=True)
    await history(serve, headers, code, criterion, [2, 4, 2, 4, 2, 4])
    _, current, _ = await serve(headers, code, criterion, 5, name="Новый", finish=False)

    default = (await check(client, headers, current, criterion, 5)).json()
    await client.patch(
        f"{API}/assignments/{assignment['id']}", headers=headers, json={"deviation_sigma": 1}
    )
    strict = (await check(client, headers, current, criterion, 5)).json()

    assert default["warn"] is False and strict["warn"] is True


@pytest.mark.asyncio
async def test_only_completed_work_counts_and_the_current_one_is_left_out(client, lesson, serve):
    headers, code, _, criterion = await lesson("gc_count", deviation_warning=True)
    await history(serve, headers, code, criterion, [3, 4, 3, 4])
    _, current, _ = await serve(headers, code, criterion, 3, name="Пятый", finish=False)
    await client.put(
        f"{API}/submissions/{current['id']}/grades",
        headers=headers,
        json={"scores": [{"criterion_id": criterion["id"], "points": 0, "comment": ""}]},
    )

    answer = (await check(client, headers, current, criterion, 0)).json()

    assert answer["samples"] == 4 and answer["warn"] is False


@pytest.mark.asyncio
async def test_each_criterion_is_judged_on_its_own_history(client, lesson, serve):
    headers, code, assignment, first = await lesson("gc_two", deviation_warning=True)
    second = (
        await client.post(
            f"{API}/assignments/{assignment['id']}/criteria",
            headers=headers,
            json={"name": "Оформление", "max_points": 5},
        )
    ).json()
    for i, points in enumerate([3, 4, 3, 4, 3, 4]):
        await join_and_finish(client, headers, code, [first, second], points, i)
    _, current, _ = await serve(headers, code, first, 3, name="Новый", finish=False)

    one = (await check(client, headers, current, first, 0)).json()
    other = (await check(client, headers, current, second, 0)).json()

    assert one["warn"] and other["warn"]
    assert [e["details"]["criterion_id"] for e in await events(client, headers, code)] == [
        first["id"],
        second["id"],
    ]


async def join_and_finish(client, headers, code, criteria, points, index):
    token = f"двойной{index}".ljust(16, "x")
    await client.post(
        f"{API}/sessions/{code}/join", json={"name": f"Двойной {index}", "token": token}
    )
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    current = (
        await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
    ).json()
    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={"scores": [{"criterion_id": c["id"], "points": points} for c in criteria]},
    )
    assert done.status_code == 200, done.text


@pytest.mark.asyncio
async def test_the_check_is_private_to_the_owner_and_validates_input(
    client, lesson, serve, make_teacher
):
    headers, code, _, criterion = await lesson("gc_owner", deviation_warning=True)
    _, current, _ = await serve(headers, code, criterion, 3, name="Новый", finish=False)
    stranger = await make_teacher(f"gc_stranger_{uuid.uuid4().hex[:6]}")

    foreign = await check(client, stranger, current, criterion, 0)
    anonymous = await client.post(
        f"{API}/submissions/{current['id']}/deviation-check",
        json={"criterion_id": criterion["id"], "points": 0},
    )
    unknown = await check(client, headers, current, {"id": "nope"}, 0)
    too_high = await check(client, headers, current, criterion, 9)

    assert foreign.status_code == 404
    assert anonymous.status_code == 401
    assert unknown.status_code == 404 and unknown.json()["code"] == "criterion_not_found"
    assert too_high.status_code == 422 and too_high.json()["code"] == "points_out_of_range"
