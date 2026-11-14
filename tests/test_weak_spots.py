import pytest

from services.analytics import SubmissionData, analyze, weak_spots

API = "/api/v1"
CRITERIA = {"code": ("Код", 10), "defence": ("Защита", 10), "tests": ("Тесты", 4)}


def sub(ticket, code, defence, tests, *, attempt=1, questions=(), duration=100):
    total = code + defence + tests
    return SubmissionData(
        ticket,
        duration,
        total,
        24,
        {"code": code, "defence": defence, "tests": tests},
        attempt,
        list(questions),
    )


KNOWN = [
    sub(1, 10, 8, 4, questions=("Что такое индекс?", "Зачем нужен ключ?")),
    sub(2, 9, 4, 1, questions=("Что такое индекс?", "Зачем нужен ключ?")),
    sub(3, 8, 3, 2, questions=("Что такое индекс?", "Как работает JOIN?")),
    sub(4, 6, 2, 0, questions=("Как работает JOIN?", "Зачем нужен ключ?")),
    sub(5, 10, 10, 4, questions=("Что такое индекс?", "Как работает JOIN?")),
    sub(6, 5, 5, 1, questions=("Зачем нужен ключ?", "Как работает JOIN?")),
]


def by_name(spots):
    return {c.name: c for c in spots.criteria}


def test_criterion_means_and_failure_shares_on_known_data():
    spots = weak_spots(KNOWN, CRITERIA, min_submissions=5)

    code, defence, tests = by_name(spots)["Код"], by_name(spots)["Защита"], by_name(spots)["Тесты"]
    assert code.mean_percent == 80.0
    assert code.below_half_percent == 0.0
    assert defence.mean_percent == 53.3
    assert defence.below_half_percent == 50.0
    assert tests.mean_percent == 50.0
    assert tests.below_half_percent == 50.0
    assert (code.scored, code.max_points) == (6, 10)


def test_weakest_criteria_are_ordered_by_mean_then_failure_share():
    spots = weak_spots(KNOWN, CRITERIA, min_submissions=5)

    assert spots.weakest_criteria == ["Тесты", "Защита", "Код"]
    assert [c.name for c in spots.criteria] == ["Тесты", "Защита", "Код"]


def test_a_tie_on_the_mean_is_broken_by_the_failure_share():
    marks = [(0, 5), (10, 5), (5, 5), (5, 5), (5, 5)]
    items = [sub(i, code, defence, 2) for i, (code, defence) in enumerate(marks, 1)]

    spots = weak_spots(items, CRITERIA, min_submissions=5)

    assert by_name(spots)["Код"].mean_percent == by_name(spots)["Защита"].mean_percent == 50.0
    assert by_name(spots)["Код"].below_half_percent == 20.0
    assert by_name(spots)["Защита"].below_half_percent == 0.0
    assert spots.weakest_criteria[0] == "Код"


def test_exactly_half_of_the_maximum_is_not_a_failure():
    items = [sub(i, 5, 5, 2) for i in range(1, 6)]

    spots = weak_spots(items, CRITERIA, min_submissions=5)

    assert all(c.below_half_percent == 0.0 for c in spots.criteria)
    assert spots.below_half_percent == 0.0


def test_distribution_counts_submissions_in_ten_percent_bins():
    spots = weak_spots(KNOWN, CRITERIA, min_submissions=5)

    counts = {(b.from_percent, b.to_percent): b.count for b in spots.distribution}
    assert sum(counts.values()) == 6
    assert len(spots.distribution) == 10
    assert counts[(90, 100)] == 2
    assert counts[(50, 60)] == 2
    assert counts[(40, 50)] == 1
    assert counts[(30, 40)] == 1
    assert counts[(80, 90)] == counts[(60, 70)] == counts[(0, 10)] == 0


def test_a_perfect_score_lands_in_the_last_bin():
    items = [sub(i, 10, 10, 4) for i in range(1, 6)]

    spots = weak_spots(items, CRITERIA, min_submissions=5)

    assert spots.distribution[-1].count == 5
    assert spots.mean_percent == 100.0


def test_question_rating_uses_the_result_of_submissions_where_the_question_fell():
    assert weak_spots(KNOWN, CRITERIA, min_submissions=5).questions == []

    spots = weak_spots(KNOWN, CRITERIA, min_submissions=4)

    rows = {q.text: q for q in spots.questions}
    assert [q.text for q in spots.questions] == [
        "Зачем нужен ключ?",
        "Как работает JOIN?",
        "Что такое индекс?",
    ]
    assert {q.asked for q in rows.values()} == {4}
    assert rows["Зачем нужен ключ?"].mean_percent == 57.3
    assert rows["Зачем нужен ключ?"].below_half_percent == 50.0
    assert rows["Как работает JOIN?"].mean_percent == 58.3
    assert rows["Как работает JOIN?"].below_half_percent == 50.0
    assert rows["Что такое индекс?"].mean_percent == 76.0
    assert rows["Что такое индекс?"].below_half_percent == 0.0


def test_questions_asked_fewer_than_the_minimum_times_are_hidden_not_shown():
    items = [sub(i, 8, 8, 3, questions=("Частый вопрос",)) for i in range(1, 7)]
    items[0].questions = ["Частый вопрос", "Редкий вопрос"]

    spots = weak_spots(items, CRITERIA, min_submissions=5)

    assert [q.text for q in spots.questions] == ["Частый вопрос"]
    assert spots.questions[0].asked == 6


def test_fewer_than_the_minimum_submissions_give_no_numbers():
    spots = weak_spots(KNOWN[:4], CRITERIA, min_submissions=5)

    assert spots.sufficient is False and spots.submissions == 4
    assert spots.criteria == [] and spots.questions == [] and spots.distribution == []
    assert spots.weakest_criteria == []
    assert spots.mean_percent is None
    assert "нужно не менее 5 сдач" in spots.note


def test_an_empty_sample_is_handled():
    spots = weak_spots([], CRITERIA, min_submissions=5)

    assert (spots.sufficient, spots.submissions) == (False, 0)
    out = analyze([], CRITERIA, min_submissions=5)
    assert out.weak_spots.sufficient is False
    assert out.first_attempts.submissions == 0 and out.retakes.submissions == 0


def test_a_work_without_criteria_is_explained_instead_of_failing():
    items = [SubmissionData(i, 60, 0, 0, {}, 1, ["Вопрос"]) for i in range(1, 7)]

    spots = weak_spots(items, {}, min_submissions=5)

    assert spots.sufficient is True
    assert spots.criteria == [] and spots.weakest_criteria == []
    assert spots.mean_percent is None and spots.distribution == [] and spots.questions == []
    assert "нет критериев" in spots.note


def test_a_criterion_scored_in_too_few_submissions_is_left_out():
    items = [sub(i, 8, 8, 3) for i in range(1, 7)]
    for item in items[:3]:
        del item.scores["tests"]

    spots = weak_spots(items, CRITERIA, min_submissions=5)

    assert [c.name for c in spots.criteria] == ["Защита", "Код"]


def test_first_attempts_and_retakes_are_counted_separately_with_their_own_minimum():
    firsts = [sub(i, 4, 4, 1, attempt=1) for i in range(1, 8)]
    retakes = [sub(10 + i, 9, 9, 4, attempt=2) for i in range(1, 4)]

    out = analyze(firsts + retakes, CRITERIA, min_submissions=5)

    assert out.weak_spots.submissions == 10
    assert out.first_attempts.sufficient is True and out.first_attempts.submissions == 7
    assert out.first_attempts.mean_percent == pytest.approx(37.5, abs=0.1)
    assert out.retakes.sufficient is False and out.retakes.submissions == 3
    assert "пересдач: 3" in out.retakes.note
    assert out.retakes.criteria == []


def test_retakes_with_enough_data_get_their_own_numbers():
    firsts = [sub(i, 4, 4, 1, attempt=1) for i in range(1, 6)]
    retakes = [sub(10 + i, 9, 9, 4, attempt=3) for i in range(1, 7)]

    out = analyze(firsts + retakes, CRITERIA, min_submissions=5)

    assert out.first_attempts.mean_percent < out.retakes.mean_percent
    assert out.retakes.mean_percent == pytest.approx(91.7, abs=0.1)
    assert out.retakes.submissions == 6


def test_insufficient_overall_sample_still_reports_the_split_without_numbers():
    out = analyze(KNOWN[:3], CRITERIA, min_submissions=5)

    assert out.sufficient is False
    assert out.weak_spots.sufficient is False
    assert out.first_attempts.submissions == 3
    assert out.retakes.submissions == 0
    assert out.duration is None


def test_weak_spots_carry_no_names_or_tickets():
    out = analyze(KNOWN, CRITERIA, min_submissions=5)

    dumped = out.weak_spots.model_dump_json()
    for forbidden in ("ticket", "name_key", "student", "Анна"):
        assert forbidden not in dumped


async def finished(client, headers, assignment, criteria, code, name, points, token, device=None):
    body = {"name": name, "token": token}
    if device:
        body["device_key"] = device
    await client.post(f"{API}/sessions/{code}/join", json=body)
    await client.post(f"{API}/sessions/{code}/next", headers=headers)
    current = (
        await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
    ).json()
    r = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=headers,
        json={
            "scores": [
                {"criterion_id": c["id"], "points": p}
                for c, p in zip(criteria, points, strict=True)
            ]
        },
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def lab(client, make_teacher, make_session):
    async def _lab(username: str, questions: int = 4):
        headers = await make_teacher(username)
        assignment = (
            await client.post(
                f"{API}/assignments",
                headers=headers,
                json={"title": f"Работа {username}", "questions_per_submission": 2},
            )
        ).json()
        for i in range(questions):
            await client.post(
                f"{API}/assignments/{assignment['id']}/questions",
                headers=headers,
                json={"text": f"Вопрос {i}"},
            )
        criteria = []
        for name, maximum in (("Код", 10), ("Защита", 10)):
            criteria.append(
                (
                    await client.post(
                        f"{API}/assignments/{assignment['id']}/criteria",
                        headers=headers,
                        json={"name": name, "max_points": maximum},
                    )
                ).json()
            )
        code = await make_session(headers)
        await client.put(
            f"{API}/sessions/{code}/assignment",
            headers=headers,
            json={"assignment_id": assignment["id"]},
        )
        return headers, assignment, criteria, code

    return _lab


@pytest.mark.asyncio
async def test_api_returns_weak_spots_for_a_session_and_an_assignment(client, lab):
    headers, assignment, criteria, code = await lab("weak_api")
    marks = [(9, 9), (8, 2), (7, 3), (10, 1), (6, 8)]
    for i, points in enumerate(marks):
        await finished(
            client,
            headers,
            assignment,
            criteria,
            code,
            f"Студент {i}",
            points,
            f"tok-weak-api-{i:04d}-xxxx",
        )

    session = (await client.get(f"{API}/sessions/{code}/analytics", headers=headers)).json()
    work = (
        await client.get(f"{API}/assignments/{assignment['id']}/analytics", headers=headers)
    ).json()

    for out in (session, work):
        spots = out["weak_spots"]
        assert spots["sufficient"] is True and spots["submissions"] == 5
        assert spots["weakest_criteria"] == ["Защита", "Код"]
        crit = {c["name"]: c for c in spots["criteria"]}
        assert crit["Код"]["mean_percent"] == 80.0
        assert crit["Защита"]["mean_percent"] == 46.0
        assert crit["Защита"]["below_half_percent"] == 60.0
        assert sum(b["count"] for b in spots["distribution"]) == 5
        assert out["first_attempts"]["submissions"] == 5
        assert out["retakes"]["submissions"] == 0
        assert "Студент" not in str(out)


@pytest.mark.asyncio
async def test_api_below_the_minimum_gives_no_weak_spot_numbers(client, lab):
    headers, assignment, criteria, code = await lab("weak_few")
    for i in range(4):
        await finished(
            client,
            headers,
            assignment,
            criteria,
            code,
            f"S{i}",
            (5, 5),
            f"tok-weak-few-{i:04d}-xxxxx",
        )

    out = (await client.get(f"{API}/sessions/{code}/analytics", headers=headers)).json()

    assert out["sufficient"] is False
    assert out["weak_spots"]["sufficient"] is False
    assert out["weak_spots"]["criteria"] == [] and out["weak_spots"]["questions"] == []


@pytest.mark.asyncio
async def test_api_separates_first_attempts_from_retakes(client, lab, make_session):
    headers, assignment, criteria, code = await lab("weak_retake")
    for i in range(5):
        await finished(
            client,
            headers,
            assignment,
            criteria,
            code,
            f"Первый {i}",
            (4, 4),
            f"tok-first-{i:04d}-xxxxxxx",
            device=f"{i}" * 24,
        )
    second_code = await make_session(headers)
    await client.put(
        f"{API}/sessions/{second_code}/assignment",
        headers=headers,
        json={"assignment_id": assignment["id"]},
    )
    for i in range(5):
        await finished(
            client,
            headers,
            assignment,
            criteria,
            second_code,
            f"Первый {i}",
            (9, 9),
            f"tok-again-{i:04d}-xxxxxxx",
            device=f"{i}" * 24,
        )

    work = (
        await client.get(f"{API}/assignments/{assignment['id']}/analytics", headers=headers)
    ).json()
    second = (await client.get(f"{API}/sessions/{second_code}/analytics", headers=headers)).json()

    assert work["weak_spots"]["submissions"] == 10
    assert work["first_attempts"]["submissions"] == 5
    assert work["first_attempts"]["mean_percent"] == 40.0
    assert work["retakes"]["submissions"] == 5
    assert work["retakes"]["mean_percent"] == 90.0
    assert second["first_attempts"]["submissions"] == 0
    assert second["retakes"]["submissions"] == 5


@pytest.mark.asyncio
async def test_api_weak_spots_follow_the_same_ownership_rules(client, lab, make_teacher):
    headers, assignment, criteria, code = await lab("weak_owner")
    stranger = await make_teacher("weak_owner_other")

    assert (
        await client.get(f"{API}/sessions/{code}/analytics", headers=stranger)
    ).status_code == 404
    assert (
        await client.get(f"{API}/assignments/{assignment['id']}/analytics", headers=stranger)
    ).status_code == 404
    assert (await client.get(f"{API}/sessions/{code}/analytics")).status_code == 401


@pytest.mark.asyncio
async def test_api_questions_need_the_minimum_number_of_appearances(client, lab):
    headers, assignment, criteria, code = await lab("weak_questions", questions=2)
    for i in range(6):
        await finished(
            client,
            headers,
            assignment,
            criteria,
            code,
            f"Q{i}",
            (i + 1, 2),
            f"tok-weak-q-{i:04d}-xxxxxx",
        )

    out = (await client.get(f"{API}/sessions/{code}/analytics", headers=headers)).json()

    questions = out["weak_spots"]["questions"]
    assert {q["text"] for q in questions} == {"Вопрос 0", "Вопрос 1"}
    assert all(q["asked"] == 6 for q in questions)
    assert questions[0]["mean_percent"] == questions[1]["mean_percent"]
