import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from core.config import settings
from services.analytics import SubmissionData, analyze, band_of, quantile

API = "/api/v1"
CRITERIA = {"c1": ("Код", 5), "c2": ("Защита", 5)}


def item(ticket, duration, total, maximum=10, scores=None):
    return SubmissionData(
        ticket, duration, total, maximum, scores or {"c1": total // 2, "c2": total - total // 2}
    )


def test_small_samples_make_no_conclusions():
    out = analyze([item(i, 100, 9) for i in range(1, 5)], CRITERIA, min_submissions=5)
    assert out.sufficient is False and out.submissions == 4
    assert out.duration is None and out.flags == [] and out.criteria == []
    assert "для выводов нужно не меньше 5" in out.note


def test_empty_sample_is_handled():
    out = analyze([], CRITERIA, min_submissions=5)
    assert (out.sufficient, out.submissions, out.duration, out.flags) == (False, 0, None, [])


def test_duration_statistics_and_the_flag_text():
    items = [item(i + 1, d, 9) for i, d in enumerate([100, 100, 110, 120, 100, 300])]
    out = analyze(items, CRITERIA, min_submissions=5, outlier_factor=2.0)
    assert out.sufficient is True
    assert out.duration.median == 105 and out.duration.min == 100 and out.duration.max == 300
    assert [f.ticket for f in out.flags] == [6]
    assert out.flags[0].ratio == pytest.approx(2.86, abs=0.01)
    assert out.flags[0].text.startswith("Сдача №6 длилась 2,9 раза дольше медианы")
    assert "студент" not in out.flags[0].text.lower()
    high = next(b for b in out.bands if b.band == "high")
    assert high.count == 6 and high.median_duration == 105


def test_outliers_are_judged_inside_the_same_result_band():
    high = [item(i, 100, 9) for i in range(1, 7)]
    low = [item(7, 50, 2), item(8, 900, 2)]
    out = analyze(high + low, CRITERIA, min_submissions=5, outlier_factor=2.0)
    assert out.flags == []
    low_band = next(b for b in out.bands if b.band == "low")
    assert low_band.count == 2 and low_band.median_duration is None


def test_a_long_submission_with_a_different_result_is_not_flagged_by_the_other_band():
    mid = [item(i, 100, 6) for i in range(1, 7)]
    out = analyze(mid + [item(7, 100, 9)], CRITERIA, min_submissions=5)
    assert out.flags == []


def test_equal_durations_have_no_flags():
    out = analyze([item(i, 120, 9) for i in range(1, 8)], CRITERIA, min_submissions=5)
    assert out.flags == []


def test_criteria_distribution_and_mean():
    items = [item(1, 60, 6, scores={"c1": 5, "c2": 1}), item(2, 60, 6, scores={"c1": 5, "c2": 1})]
    items += [item(3, 60, 4, scores={"c1": 2, "c2": 2}), item(4, 60, 10, scores={"c1": 5, "c2": 5})]
    items += [item(5, 60, 0, scores={"c1": 0, "c2": 0})]
    out = analyze(items, CRITERIA, min_submissions=5)
    code = next(c for c in out.criteria if c.name == "Код")
    assert code.mean == 3.4 and code.distribution == {
        "0": 1,
        "1": 0,
        "2": 1,
        "3": 0,
        "4": 0,
        "5": 3,
    }


def test_quantile_and_bands():
    assert quantile([10, 20, 30, 40], 0.25) == 17.5
    assert quantile([10, 20, 30, 40], 0.75) == 32.5
    assert quantile([7], 0.5) == 7.0
    assert [band_of(t, 10) for t in (0, 4, 5, 7, 8, 10)] == [
        "low",
        "low",
        "mid",
        "mid",
        "high",
        "high",
    ]
    assert band_of(0, 0) == "low"


@pytest_asyncio.fixture
async def finished(client, make_teacher, make_session, join, db_session):
    async def _build(n: int, durations: list[int], points: list[int] | None = None):
        headers = await make_teacher(f"an_{uuid.uuid4().hex[:8]}")
        a = (
            await client.post(f"{API}/assignments", headers=headers, json={"title": "Аналитика"})
        ).json()
        crit = (
            await client.post(
                f"{API}/assignments/{a['id']}/criteria",
                headers=headers,
                json={"name": "Код", "max_points": 10},
            )
        ).json()
        code = await make_session(headers)
        await client.put(
            f"{API}/sessions/{code}/assignment", headers=headers, json={"assignment_id": a["id"]}
        )
        names = []
        for i in range(n):
            names.append(f"СекретноеИмя{i}")
            await join(code, names[-1])
        ids = []
        for i in range(n):
            await client.post(f"{API}/sessions/{code}/next", headers=headers)
            sub = (
                await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
            ).json()
            value = (points or [10] * n)[i]
            r = await client.post(
                f"{API}/submissions/{sub['id']}/complete",
                headers=headers,
                json={"scores": [{"criterion_id": crit["id"], "points": value}]},
            )
            assert r.status_code == 200, r.text
            ids.append(sub["id"])
        for sid, d in zip(ids, durations, strict=True):
            await db_session.execute(
                text("UPDATE submissions SET duration_seconds = :d WHERE id = :i"),
                {"d": d, "i": sid},
            )
        await db_session.commit()
        return {"headers": headers, "assignment": a, "code": code, "names": names}

    return _build


@pytest.mark.asyncio
async def test_session_and_assignment_analytics_are_anonymous_aggregates(
    client, finished, monkeypatch
):
    monkeypatch.setattr(settings, "ANALYTICS_MIN_SUBMISSIONS", 5)
    ctx = await finished(6, [100, 100, 110, 120, 100, 300])
    h = ctx["headers"]

    r = await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=h)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["sufficient"] is True and body["submissions"] == 6
    assert body["duration"]["median"] == 105
    assert [f["ticket"] for f in body["flags"]] == [6]
    assert "Сдача №6" in body["flags"][0]["text"]
    dumped = json.dumps(body, ensure_ascii=False)
    assert not any(name in dumped for name in ctx["names"])

    r2 = await client.get(f"{API}/assignments/{ctx['assignment']['id']}/analytics", headers=h)
    assert r2.json() == body


@pytest.mark.asyncio
async def test_threshold_comes_from_settings(client, finished, monkeypatch):
    ctx = await finished(3, [60, 70, 80])
    monkeypatch.setattr(settings, "ANALYTICS_MIN_SUBMISSIONS", 4)
    body = (
        await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=ctx["headers"])
    ).json()
    assert body["sufficient"] is False and body["duration"] is None and body["min_submissions"] == 4
    monkeypatch.setattr(settings, "ANALYTICS_MIN_SUBMISSIONS", 3)
    body = (
        await client.get(f"{API}/sessions/{ctx['code']}/analytics", headers=ctx["headers"])
    ).json()
    assert body["sufficient"] is True


@pytest.mark.asyncio
async def test_analytics_of_someone_elses_work_is_404(client, finished, make_teacher, make_session):
    ctx = await finished(1, [60])
    other = await make_teacher(f"an_x_{uuid.uuid4().hex[:6]}")
    for url in (
        f"/assignments/{ctx['assignment']['id']}/analytics",
        f"/sessions/{ctx['code']}/analytics",
    ):
        assert (await client.get(API + url, headers=other)).status_code == 404
    assert (await client.get(f"{API}/sessions/{ctx['code']}/analytics")).status_code == 401
    bare = await make_session(other)
    r = await client.get(f"{API}/sessions/{bare}/analytics", headers=other)
    assert r.status_code == 404
