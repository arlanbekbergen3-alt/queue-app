import json
import uuid

import pytest

from core.config import settings
from services.fairness import (
    Rated,
    attempts_block,
    order_block,
    peers_block,
    quarter_of,
    spread_block,
)

API = "/api/v1"


def rated(shares, session="s", attempt=1):
    return [Rated(session_id=session, share=s, attempt_number=attempt) for s in shares]


def test_positions_map_onto_four_queue_quarters():
    assert [quarter_of(p, 8) for p in range(8)] == [0, 0, 1, 1, 2, 2, 3, 3]
    assert [quarter_of(p, 5) for p in range(5)] == [0, 0, 1, 2, 3]
    assert quarter_of(0, 1) == 0


def test_a_clearly_lower_last_quarter_raises_the_flag():
    items = rated([1.0, 1.0, 0.9, 0.9, 0.7, 0.7, 0.4, 0.4])

    block = order_block(items, 10.0)

    assert [q.count for q in block.quarters] == [2, 2, 2, 2]
    assert block.quarters[0].mean_percent == 100.0 and block.quarters[3].mean_percent == 40.0
    assert block.drop_percent == 60.0 and block.fatigue_flag is True


def test_a_small_drop_stays_below_the_threshold_and_a_rise_is_not_flagged():
    small = order_block(rated([1.0, 1.0, 0.9, 0.9, 0.9, 0.9, 0.95, 0.95]), 10.0)
    rising = order_block(rated([0.4, 0.4, 0.5, 0.5, 0.7, 0.7, 1.0, 1.0]), 10.0)

    assert small.drop_percent == 5.0 and small.fatigue_flag is False
    assert rising.drop_percent == -60.0 and rising.fatigue_flag is False


def test_the_threshold_is_inclusive_and_follows_the_argument():
    items = rated([1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.9, 0.9])

    assert order_block(items, 10.0).fatigue_flag is True
    assert order_block(items, 10.1).fatigue_flag is False


def test_no_flag_when_the_first_or_last_quarter_holds_a_single_result():
    block = order_block(rated([1.0, 0.9, 0.8, 0.7, 0.1]), 10.0)

    assert [q.count for q in block.quarters] == [2, 1, 1, 1]
    assert block.drop_percent is None and block.fatigue_flag is False


def test_each_session_is_split_into_quarters_on_its_own():
    first = rated([1.0, 1.0, 1.0, 1.0], session="a")
    second = rated([0.2, 0.2, 0.2, 0.2], session="b")

    block = order_block(first + second, 10.0)

    assert [q.count for q in block.quarters] == [2, 2, 2, 2]
    assert [q.mean_percent for q in block.quarters] == [60.0, 60.0, 60.0, 60.0]
    assert block.fatigue_flag is False


def test_first_attempts_and_retakes_are_separate_and_need_the_minimum_each():
    items = rated([1.0] * 5) + rated([0.5] * 3, attempt=2) + rated([0.0] * 1, attempt=3)

    block = attempts_block(items, 5)

    assert block.first.mean_percent == 100.0 and block.first.count == 5
    assert block.retakes.sufficient is False and block.retakes.mean_percent is None
    assert block.difference_percent is None


def test_the_difference_is_shown_only_when_both_groups_are_large_enough():
    items = rated([0.8] * 5) + rated([0.6] * 5, attempt=2)

    block = attempts_block(items, 5)

    assert block.first.mean_percent == 80.0 and block.retakes.mean_percent == 60.0
    assert block.difference_percent == -20.0


def test_spread_reports_deviation_and_the_share_of_extreme_marks_per_criterion():
    criteria = {"a": ("Ответ", 4), "b": ("Редкий", 4)}
    scores = {"a": [0, 4, 4, 2, 1], "b": [1, 2]}

    block = spread_block(scores, criteria, 5)

    assert [row.name for row in block.rows] == ["Ответ"]
    row = block.rows[0]
    assert row.scored == 5 and row.mean_percent == 55.0
    assert row.zero_percent == 20.0 and row.top_percent == 40.0 and row.extreme_percent == 60.0
    assert row.stdev_percent == 44.7
    assert block.extreme_percent == 60.0


def own_rows(*items):
    return {(name, maximum): (mean, count) for name, maximum, mean, count in items}


def others(*items):
    return [
        (f"x{i}", name, maximum, mean, count)
        for i, (name, maximum, mean, count) in enumerate(items)
    ]


def test_peer_values_are_listed_without_labels_in_ascending_order():
    block = peers_block(
        enabled=True,
        has_origin=True,
        own=own_rows(("Ответ", 4, 3.0, 8)),
        others=others(("Ответ", 4, 2.0, 6), ("Ответ", 4, 1.0, 9), ("Ответ", 4, 3.5, 5)),
        minimum=5,
    )

    assert block.available is True and block.note == ""
    assert block.rows[0].own_percent == 75.0
    assert block.rows[0].peer_percents == [25.0, 50.0, 87.5] and block.rows[0].peers == 3


def test_peers_with_too_few_results_and_mismatched_criteria_are_left_out():
    block = peers_block(
        enabled=True,
        has_origin=True,
        own=own_rows(("Ответ", 4, 3.0, 8)),
        others=others(("Ответ", 4, 2.0, 4), ("Ответ", 10, 5.0, 9), ("Ответ", 4, 1.0, 9)),
        minimum=5,
    )

    assert block.rows == [] and "Недостаточно данных" in block.note


def test_the_comparison_needs_the_minimum_number_of_other_participants(monkeypatch):
    monkeypatch.setattr(settings, "FAIRNESS_MIN_PEERS", 3)
    base = dict(enabled=True, has_origin=True, own=own_rows(("Ответ", 4, 3.0, 8)), minimum=5)

    two = peers_block(others=others(("Ответ", 4, 2.0, 6), ("Ответ", 4, 1.0, 9)), **base)
    three = peers_block(
        others=others(("Ответ", 4, 2.0, 6), ("Ответ", 4, 1.0, 9), ("Ответ", 4, 4.0, 7)), **base
    )

    assert two.rows == [] and len(three.rows) == 1


def test_your_own_criterion_with_too_few_scores_is_not_compared():
    block = peers_block(
        enabled=True,
        has_origin=True,
        own=own_rows(("Ответ", 4, 3.0, 3)),
        others=others(("Ответ", 4, 2.0, 6), ("Ответ", 4, 1.0, 9)),
        minimum=5,
    )

    assert block.rows == []


def test_without_consent_or_a_group_origin_nothing_is_compared():
    off = peers_block(enabled=False, has_origin=True, own={}, others=[], minimum=5)
    lonely = peers_block(enabled=True, has_origin=False, own={}, others=[], minimum=5)

    assert off.enabled is False and off.available is False and off.rows == []
    assert lonely.available is False and "предметной группы" in lonely.note


@pytest.fixture
def teacher(make_teacher):
    async def _teacher(prefix: str):
        name = f"{prefix}_{uuid.uuid4().hex[:6]}"
        return name, await make_teacher(name)

    return _teacher


async def make_work(client, headers, title, *, criteria=(("Ответ", 4),)):
    made = await client.post(
        f"{API}/assignments",
        headers=headers,
        json={"title": title, "questions_per_submission": 1},
    )
    assert made.status_code == 201, made.text
    assignment = made.json()
    await client.post(
        f"{API}/assignments/{assignment['id']}/questions",
        headers=headers,
        json={"text": "Вопрос", "difficulty": 1, "topic": "t"},
    )
    for name, maximum in criteria:
        await client.post(
            f"{API}/assignments/{assignment['id']}/criteria",
            headers=headers,
            json={"name": name, "max_points": maximum},
        )
    return assignment


async def attach(client, headers, assignment_id):
    created = await client.post(f"{API}/sessions", headers=headers, json={"title": "Занятие"})
    code = created.json()["code"]
    await client.put(
        f"{API}/sessions/{code}/assignment", headers=headers, json={"assignment_id": assignment_id}
    )
    return code


async def grade(client, headers, code, assignment_id, marks, *, names=None):
    criteria = (
        await client.get(f"{API}/assignments/{assignment_id}/criteria", headers=headers)
    ).json()
    for index, mark in enumerate(marks):
        name = names[index] if names else f"Студент {uuid.uuid4().hex[:6]}"
        joined = await client.post(
            f"{API}/sessions/{code}/join", json={"name": name, "token": uuid.uuid4().hex}
        )
        assert joined.status_code == 200, joined.text
        await client.post(f"{API}/sessions/{code}/next", headers=headers)
        current = (
            await client.get(f"{API}/sessions/{code}/submissions/current", headers=headers)
        ).json()
        done = await client.post(
            f"{API}/submissions/{current['id']}/complete",
            headers=headers,
            json={"scores": [{"criterion_id": c["id"], "points": mark} for c in criteria]},
        )
        assert done.status_code == 200, done.text


async def fairness(client, headers, path):
    response = await client.get(path, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["fairness"]


@pytest.mark.asyncio
async def test_too_few_results_give_the_data_is_scarce_note_and_no_numbers(client, teacher):
    _, headers = await teacher("fr_few")
    work = await make_work(client, headers, "Мало")
    code = await attach(client, headers, work["id"])
    await grade(client, headers, code, work["id"], [4, 3, 2, 1])

    block = await fairness(client, headers, f"{API}/assignments/{work['id']}/analytics")

    assert block["sufficient"] is False and "Недостаточно данных" in block["note"]
    assert block["order"] is None and block["attempts"] is None and block["spread"] is None
    assert block["disclaimer"]


@pytest.mark.asyncio
async def test_a_session_with_a_tiring_queue_is_flagged_in_both_analytics(client, teacher):
    _, headers = await teacher("fr_tired")
    work = await make_work(client, headers, "Усталость")
    code = await attach(client, headers, work["id"])
    await grade(client, headers, code, work["id"], [4, 4, 4, 4, 3, 3, 1, 1])

    by_session = await fairness(client, headers, f"{API}/sessions/{code}/analytics")
    by_work = await fairness(client, headers, f"{API}/assignments/{work['id']}/analytics")

    for block in (by_session, by_work):
        assert block["sufficient"] is True
        assert block["order"]["fatigue_flag"] is True
        assert block["order"]["drop_percent"] == 75.0
        assert block["order"]["hint"] and block["spread"]["hint"] and block["attempts"]["hint"]
        assert block["spread"]["rows"][0]["top_percent"] == 50.0
        assert block["spread"]["extreme_percent"] == 50.0


@pytest.mark.asyncio
async def test_the_fatigue_threshold_comes_from_the_settings(client, teacher, monkeypatch):
    _, headers = await teacher("fr_threshold")
    work = await make_work(client, headers, "Порог")
    code = await attach(client, headers, work["id"])
    await grade(client, headers, code, work["id"], [4, 4, 4, 4, 4, 4, 3, 3])
    url = f"{API}/assignments/{work['id']}/analytics"

    flagged = await fairness(client, headers, url)
    monkeypatch.setattr(settings, "FAIRNESS_FATIGUE_DROP_PERCENT", 30.0)
    quiet = await fairness(client, headers, url)

    assert flagged["order"]["fatigue_flag"] is True
    assert quiet["order"]["fatigue_flag"] is False and quiet["order"]["threshold_percent"] == 30.0


@pytest.mark.asyncio
async def test_retakes_are_compared_with_first_attempts(client, teacher):
    _, headers = await teacher("fr_retake")
    work = await make_work(client, headers, "Пересдача")
    first = await attach(client, headers, work["id"])
    second = await attach(client, headers, work["id"])
    names = [f"Повторник {i}" for i in range(5)]
    await grade(client, headers, first, work["id"], [2] * 5, names=names)
    await grade(client, headers, second, work["id"], [4] * 5, names=names)

    block = await fairness(client, headers, f"{API}/assignments/{work['id']}/analytics")

    assert block["attempts"]["first"]["count"] == 5
    assert block["attempts"]["retakes"]["count"] == 5
    assert block["attempts"]["first"]["mean_percent"] == 50.0
    assert block["attempts"]["retakes"]["mean_percent"] == 100.0
    assert block["attempts"]["difference_percent"] == 50.0


@pytest.mark.asyncio
async def test_only_the_owner_can_read_the_block(client, teacher):
    _, headers = await teacher("fr_owner")
    _, stranger = await teacher("fr_stranger")
    work = await make_work(client, headers, "Чужая")
    code = await attach(client, headers, work["id"])

    foreign_work = await client.get(f"{API}/assignments/{work['id']}/analytics", headers=stranger)
    foreign_session = await client.get(f"{API}/sessions/{code}/analytics", headers=stranger)
    anonymous = await client.get(f"{API}/assignments/{work['id']}/analytics")

    assert foreign_work.status_code == 404
    assert foreign_session.status_code == 404
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_the_block_holds_no_names_or_identifiers_of_students(client, teacher):
    _, headers = await teacher("fr_private")
    work = await make_work(client, headers, "Приватность")
    code = await attach(client, headers, work["id"])
    names = [f"Секретный Студент{i}" for i in range(6)]
    await grade(client, headers, code, work["id"], [4, 3, 2, 4, 3, 2], names=names)
    submissions = (await client.get(f"{API}/sessions/{code}/submissions", headers=headers)).json()
    queue = (await client.get(f"{API}/sessions/{code}/queue", headers=headers)).json()

    block = await fairness(client, headers, f"{API}/assignments/{work['id']}/analytics")
    text = json.dumps(block, ensure_ascii=False)

    assert not any(name in text for name in names)
    assert not any(entry["id"] in text for entry in queue["entries"])
    assert not any(s["id"] in text or s["entry_id"] in text for s in submissions)
    assert set(block) == {
        "submissions",
        "min_submissions",
        "sufficient",
        "note",
        "disclaimer",
        "order",
        "attempts",
        "spread",
        "peers",
    }


async def group_with_copies(client, teacher, count: int, *, marks, consent):
    owner_name, owner = await teacher("fr_space_owner")
    source = await make_work(client, owner, "Общая работа")
    space = (await client.post(f"{API}/spaces", headers=owner, json={"name": "Предмет"})).json()
    published = (
        await client.post(
            f"{API}/spaces/{space['id']}/publications",
            headers=owner,
            json={"assignment_id": source["id"]},
        )
    ).json()
    members = []
    for index in range(count):
        name, headers = await teacher(f"fr_member{index}")
        await client.post(
            f"{API}/spaces/join", headers=headers, json={"code": space["invite_code"]}
        )
        copy = (
            await client.post(
                f"{API}/spaces/{space['id']}/publications/{published['id']}/copy",
                headers=headers,
                json={},
            )
        ).json()
        code = await attach(client, headers, copy["id"])
        await grade(client, headers, code, copy["id"], marks[index])
        if consent[index]:
            await client.patch(
                f"{API}/assignments/{copy['id']}", headers=headers, json={"fairness_sharing": True}
            )
        members.append((name, headers, copy))
    return owner_name, members


@pytest.mark.asyncio
async def test_peers_see_anonymous_means_when_everyone_agreed(client, teacher):
    _, members = await group_with_copies(
        client,
        teacher,
        3,
        marks=[[4, 4, 3, 4, 3], [2, 2, 2, 2, 2], [1, 1, 1, 1, 1]],
        consent=[True, True, True],
    )
    names = [m[0] for m in members]
    ids = [m[2]["id"] for m in members]

    block = await fairness(client, members[0][1], f"{API}/assignments/{ids[0]}/analytics")
    text = json.dumps(block, ensure_ascii=False)

    peers = block["peers"]
    assert peers["enabled"] is True and peers["available"] is True and peers["note"] == ""
    row = peers["rows"][0]
    assert row["name"] == "Ответ" and row["own_percent"] == 90.0
    assert row["peers"] == 2 and row["peer_percents"] == [25.0, 50.0]
    assert not any(n in text for n in names) and not any(i in text for i in ids)


@pytest.mark.asyncio
async def test_someone_who_did_not_agree_is_never_included_and_sees_nothing(client, teacher):
    _, members = await group_with_copies(
        client,
        teacher,
        3,
        marks=[[4, 4, 3, 4, 3], [2, 2, 2, 2, 2], [1, 1, 1, 1, 1]],
        consent=[True, True, False],
    )

    agreeing = await fairness(
        client, members[0][1], f"{API}/assignments/{members[0][2]['id']}/analytics"
    )
    refusing = await fairness(
        client, members[2][1], f"{API}/assignments/{members[2][2]['id']}/analytics"
    )

    assert agreeing["peers"]["rows"] == [] and "Недостаточно данных" in agreeing["peers"]["note"]
    assert refusing["peers"]["enabled"] is False and refusing["peers"]["rows"] == []


@pytest.mark.asyncio
async def test_a_participant_with_too_few_results_does_not_count(client, teacher):
    _, members = await group_with_copies(
        client,
        teacher,
        3,
        marks=[[4, 4, 3, 4, 3], [2, 2, 2, 2, 2], [1, 1, 1]],
        consent=[True, True, True],
    )

    block = await fairness(
        client, members[0][1], f"{API}/assignments/{members[0][2]['id']}/analytics"
    )

    assert block["peers"]["rows"] == []


@pytest.mark.asyncio
async def test_a_plain_assignment_has_no_group_comparison(client, teacher):
    _, headers = await teacher("fr_plain")
    work = await make_work(client, headers, "Своя")
    await client.patch(
        f"{API}/assignments/{work['id']}", headers=headers, json={"fairness_sharing": True}
    )

    block = await fairness(client, headers, f"{API}/assignments/{work['id']}/analytics")

    assert block["peers"]["enabled"] is True and block["peers"]["available"] is False
    assert block["peers"]["rows"] == []


@pytest.mark.asyncio
async def test_consent_is_off_by_default_and_is_not_copied_with_the_work(client, teacher):
    _, headers = await teacher("fr_default")
    work = await make_work(client, headers, "Согласие")
    assert work["fairness_sharing"] is False
    await client.patch(
        f"{API}/assignments/{work['id']}", headers=headers, json={"fairness_sharing": True}
    )

    copy = (
        await client.post(f"{API}/assignments/{work['id']}/copy", headers=headers, json={})
    ).json()

    assert copy["fairness_sharing"] is False
