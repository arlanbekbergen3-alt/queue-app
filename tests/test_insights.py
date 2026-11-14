import pytest

from core.config import settings
from services.insights import (
    MAX_INSIGHTS,
    CriterionInput,
    InsightInput,
    build_insights,
    note_for,
    plural_ru,
)
from tests.conftest import API
from tests.test_protocol import call_next

MIN = 5


def items(count=8, percent=70.0, seconds=300):
    return [InsightInput(total_percent=percent, duration_seconds=seconds) for _ in range(count)]


def criterion(name, points, maximum=5):
    return CriterionInput(name=name, max_points=maximum, points=points)


def keys(out):
    return [i.key for i in out.items]


def test_a_small_sample_gives_a_note_and_no_conclusions():
    out = build_insights(items(4), [criterion("Код", [3] * 4)], minimum=MIN)
    assert out.sufficient is False and out.items == []
    assert out.note == note_for(4, MIN)
    assert "не менее 5" in out.note and "сейчас 4" in out.note


def test_the_weakest_criterion_is_named_with_its_numbers():
    code = criterion("Код", [4, 4, 5, 3, 4, 4])
    defense = criterion("Защита", [1, 2, 0, 1, 3, 4])
    out = build_insights(items(6), [code, defense], minimum=MIN)
    weakest = next(i for i in out.items if i.key == "weakest")
    assert weakest.severity == "warn"
    assert "«Защита»" in weakest.text
    assert "36,7%" in weakest.text and "66,7%" in weakest.text
    assert weakest.action


def test_a_strong_weakest_criterion_is_reported_as_good_news():
    out = build_insights(
        items(6),
        [criterion("Код", [5, 5, 4, 5, 5, 4]), criterion("Защита", [5, 4, 5, 5, 5, 5])],
        minimum=MIN,
    )
    weakest = next(i for i in out.items if i.key == "weakest")
    assert weakest.severity == "info"
    assert "уверенно" in weakest.text


def test_a_criterion_with_equal_scores_does_not_distinguish_students():
    out = build_insights(
        items(6), [criterion("Код", [5] * 6), criterion("Защита", [1, 2, 3, 4, 5, 3])], minimum=MIN
    )
    flat = next(i for i in out.items if i.key == "flat")
    assert "«Код»" in flat.text and "не различает" in flat.text
    assert "5 баллов" in flat.text


def test_several_flat_criteria_are_listed_together():
    out = build_insights(
        items(6), [criterion("Код", [3] * 6), criterion("Защита", [2] * 6)], minimum=MIN
    )
    flat = next(i for i in out.items if i.key == "flat")
    assert "«Код»" in flat.text and "«Защита»" in flat.text


def test_zero_variance_below_the_minimum_sample_is_not_reported():
    out = build_insights(items(6), [criterion("Код", [3] * 4)], minimum=MIN)
    assert "flat" not in keys(out)


def test_many_zeros_are_reported_at_the_threshold_and_not_below_it():
    at = [0] * 3 + [3] * 7
    below = [0] * 2 + [3] * 8
    assert settings.INSIGHT_ZEROS_PERCENT == 30
    found = build_insights(items(10), [criterion("Код", at)], minimum=MIN)
    quiet = build_insights(items(10), [criterion("Код", below)], minimum=MIN)
    zeros = next(i for i in found.items if i.key == "zeros")
    assert "30%" in zeros.text and "«Код»" in zeros.text
    assert "zeros" not in keys(quiet)


def test_extreme_scores_are_reported_at_the_threshold_and_not_below_it():
    at = [0, 5, 0, 5, 0, 5] + [3] * 4
    below = [0, 5, 0, 5, 0] + [3] * 5
    assert settings.INSIGHT_EXTREME_PERCENT == 60
    found = build_insights(items(10), [criterion("Код", at)], minimum=MIN)
    quiet = build_insights(items(10), [criterion("Код", below)], minimum=MIN)
    assert "extremes" in keys(found)
    assert "extremes" not in keys(quiet)


def test_the_pace_is_compared_with_the_plan_at_the_boundaries():
    plan = settings.DEFAULT_SERVICE_SECONDS
    slow = build_insights(
        items(6, seconds=round(plan * 1.3)), [criterion("Код", [3] * 6)], minimum=MIN
    )
    fast = build_insights(
        items(6, seconds=round(plan * 0.6)), [criterion("Код", [3] * 6)], minimum=MIN
    )
    even = build_insights(items(6, seconds=plan), [criterion("Код", [3] * 6)], minimum=MIN)
    assert next(i for i in slow.items if i.key == "pace").severity == "warn"
    assert "дольше" in next(i for i in slow.items if i.key == "pace").text
    assert "быстрее" in next(i for i in fast.items if i.key == "pace").text
    assert "укладываетесь" in next(i for i in even.items if i.key == "pace").text


def test_a_fatigue_flag_becomes_a_cautious_conclusion():
    out = build_insights(
        items(6),
        [criterion("Код", [1, 2, 3, 4, 5, 3])],
        minimum=MIN,
        fatigue_drop=14.5,
        fatigue_flag=True,
    )
    fatigue = next(i for i in out.items if i.key == "fatigue")
    assert "14,5 п. п." in fatigue.text
    assert "не доказательство" in fatigue.action
    quiet = build_insights(
        items(6),
        [criterion("Код", [1, 2, 3, 4, 5, 3])],
        minimum=MIN,
        fatigue_drop=14.5,
        fatigue_flag=False,
    )
    assert "fatigue" not in keys(quiet)


def test_there_are_never_more_than_five_and_warnings_come_first():
    crowded = [criterion("Код", [0, 0, 0, 5, 5, 5, 5, 5, 5, 5]), criterion("Защита", [3] * 10)]
    out = build_insights(
        items(10, seconds=900), crowded, minimum=MIN, fatigue_drop=20.0, fatigue_flag=True
    )
    assert 3 <= len(out.items) <= MAX_INSIGHTS
    severities = [i.severity for i in out.items]
    assert severities == sorted(severities, key=lambda s: 0 if s == "warn" else 1)


def test_a_quiet_group_still_gets_at_least_three_short_conclusions():
    out = build_insights(
        items(8, percent=72.0),
        [criterion("Код", [3, 4, 3, 4, 3, 4, 3, 4]), criterion("Защита", [4, 3, 4, 3, 4, 3, 4, 3])],
        minimum=MIN,
    )
    assert len(out.items) >= 3
    assert "overall" in keys(out)
    assert all(i.text and i.action for i in out.items)


def test_a_criterion_without_enough_scores_is_ignored_everywhere():
    out = build_insights(items(6), [criterion("Новый", [0, 0])], minimum=MIN)
    assert all("Новый" not in i.text for i in out.items)


def test_russian_plural_forms():
    forms = [plural_ru(n, "балл", "балла", "баллов") for n in (1, 2, 5, 11, 12, 21, 22, 25)]
    assert forms == ["балл", "балла", "баллов", "баллов", "баллов", "балл", "балла", "баллов"]


async def finish(client, setup, name, marks, **kwargs):
    ctx = await setup(name, students=len(marks), **kwargs)
    for mark in marks:
        sub = await call_next(client, ctx)
        scores = [{"criterion_id": c["id"], "points": mark} for c in ctx["criteria"]]
        done = await client.post(
            f"{API}/submissions/{sub['id']}/complete",
            headers=ctx["headers"],
            json={"scores": scores, "comment": ""},
        )
        assert done.status_code == 200, done.text
    return ctx


@pytest.mark.asyncio
async def test_the_session_endpoint_returns_short_conclusions_without_names(client, setup):
    ctx = await finish(
        client, setup, "ins_api", [0, 0, 1, 5, 5, 3, 0, 5], criteria=(("Код", 5), ("Защита", 5))
    )

    response = await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=ctx["headers"])
    body = response.json()

    assert response.status_code == 200
    insights = body["insights"]
    assert insights["sufficient"] is True
    assert 3 <= len(insights["items"]) <= MAX_INSIGHTS
    assert {"key", "severity", "text", "action"} <= set(insights["items"][0])
    assert "ins_api-s" not in str(insights)


@pytest.mark.asyncio
async def test_the_assignment_endpoint_has_the_same_conclusions(client, setup):
    ctx = await finish(client, setup, "ins_assign", [2, 3, 2, 3, 2, 3])

    response = await client.get(
        f"{API}/assignments/{ctx['assignment']['id']}/analytics", headers=ctx["headers"]
    )

    assert response.json()["insights"]["sufficient"] is True


@pytest.mark.asyncio
async def test_with_a_small_sample_the_endpoint_says_data_is_scarce(client, setup):
    ctx = await finish(client, setup, "ins_small", [2, 3, 4])

    response = await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=ctx["headers"])
    insights = response.json()["insights"]

    assert insights["sufficient"] is False and insights["items"] == []
    assert "не менее 5" in insights["note"]


@pytest.mark.asyncio
async def test_only_the_owner_sees_the_conclusions(client, setup, make_teacher):
    ctx = await finish(client, setup, "ins_owner", [2, 3, 2, 3, 2, 3])
    stranger = await make_teacher("ins_stranger")

    foreign = await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=stranger)

    assert foreign.status_code in (403, 404)
    assert "insights" not in foreign.text
