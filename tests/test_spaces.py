import asyncio

import pytest
from sqlalchemy import func, select

from core.config import settings
from models.space import PublicationORM, SpaceMemberORM
from services.queue import CODE_ALPHABET
from services.spaces import INVITE_LENGTH, new_invite_code, normalize_invite

API = "/api/v1"


def test_invite_codes_use_the_safe_alphabet_and_are_not_repeated():
    codes = {new_invite_code() for _ in range(200)}

    assert len(codes) == 200
    assert all(len(c) == INVITE_LENGTH and set(c) <= set(CODE_ALPHABET) for c in codes)
    assert normalize_invite("  abc234  ") == "ABC234"


@pytest.fixture
def world(client, make_teacher):
    class World:
        async def teacher(self, name):
            return await make_teacher(name)

        async def space(self, headers, name="Базы данных"):
            r = await client.post(f"{API}/spaces", headers=headers, json={"name": name})
            assert r.status_code == 201, r.text
            return r.json()

        async def join(self, headers, code):
            return await client.post(f"{API}/spaces/join", headers=headers, json={"code": code})

        async def member(self, owner, space, name):
            headers = await make_teacher(name)
            r = await self.join(headers, space["invite_code"])
            assert r.status_code == 200, r.text
            return headers

        async def assignment(self, headers, title="Лабораторная", questions=2, criteria=True):
            made = await client.post(
                f"{API}/assignments",
                headers=headers,
                json={"title": title, "questions_per_submission": 1},
            )
            assert made.status_code == 201, made.text
            assignment = made.json()
            for i in range(questions):
                await client.post(
                    f"{API}/assignments/{assignment['id']}/questions",
                    headers=headers,
                    json={"text": f"{title}: вопрос {i}", "difficulty": 2, "topic": "Тема"},
                )
            if criteria:
                for name, maximum in (("Код", 10), ("Защита", 5)):
                    await client.post(
                        f"{API}/assignments/{assignment['id']}/criteria",
                        headers=headers,
                        json={"name": name, "max_points": maximum},
                    )
            return assignment

        async def publish(self, headers, space, assignment, title=None):
            body = {"assignment_id": assignment["id"]}
            if title:
                body["title"] = title
            return await client.post(
                f"{API}/spaces/{space['id']}/publications", headers=headers, json=body
            )

    return World()


@pytest.mark.asyncio
async def test_owner_creates_a_space_and_alone_sees_the_invite_code(client, world):
    owner = await world.teacher("sp_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_member")

    assert space["role"] == "owner" and space["members_count"] == 1
    assert len(space["invite_code"]) == INVITE_LENGTH
    seen = (await client.get(f"{API}/spaces/{space['id']}", headers=member)).json()
    assert seen["role"] == "member" and seen["invite_code"] is None
    assert seen["members_count"] == 2
    assert sorted(m["username"] for m in seen["members"]) == ["sp_member", "sp_owner"]
    assert [m["is_owner"] for m in seen["members"] if m["username"] == "sp_owner"] == [True]
    listed = (await client.get(f"{API}/spaces", headers=member)).json()
    assert [s["id"] for s in listed] == [space["id"]] and listed[0]["invite_code"] is None


@pytest.mark.asyncio
async def test_joining_needs_a_valid_code_and_is_idempotent(client, world):
    owner = await world.teacher("sp_join_owner")
    space = await world.space(owner)
    guest = await world.teacher("sp_join_guest")

    bad = await world.join(guest, "WRONGCODE1")
    sloppy = await world.join(guest, f"  {space['invite_code'].lower()} ")
    again = await world.join(guest, space["invite_code"])
    anonymous = await client.post(f"{API}/spaces/join", json={"code": space["invite_code"]})

    assert bad.status_code == 404 and bad.json()["code"] == "space_code_invalid"
    assert bad.json()["detail"] == "Неверный код приглашения"
    assert sloppy.status_code == 200 and sloppy.json()["role"] == "member"
    assert again.status_code == 200 and again.json()["members_count"] == 2
    assert anonymous.status_code == 401


@pytest.mark.asyncio
async def test_a_non_member_cannot_see_anything_in_a_space(client, world):
    owner = await world.teacher("sp_hidden_owner")
    space = await world.space(owner)
    published = (await world.publish(owner, space, await world.assignment(owner))).json()
    stranger = await world.teacher("sp_hidden_stranger")
    base = f"{API}/spaces/{space['id']}"

    for method, url in (
        ("get", base),
        ("get", f"{base}/publications"),
        ("get", f"{base}/publications/{published['id']}"),
        ("delete", base),
        ("delete", f"{base}/members/me"),
        ("delete", f"{base}/publications/{published['id']}"),
        ("post", f"{base}/invite"),
    ):
        r = await getattr(client, method)(url, headers=stranger)
        assert (r.status_code, r.json()["code"]) == (404, "space_not_found"), url
    copy = await client.post(
        f"{base}/publications/{published['id']}/copy", headers=stranger, json={}
    )
    assert copy.status_code == 404
    assert (await client.get(f"{API}/spaces", headers=stranger)).json() == []
    assert (await client.get(base)).status_code == 401


@pytest.mark.asyncio
async def test_publication_is_a_snapshot_of_criteria_and_questions_only(client, world):
    owner = await world.teacher("sp_snap_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_snap_member")
    assignment = await world.assignment(owner, "Снимок")

    r = await world.publish(owner, space, assignment, title="Банк по JOIN")

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["title"] == "Банк по JOIN" and body["author"] == "sp_snap_owner"
    assert body["mine"] is True and (body["criteria_count"], body["questions_count"]) == (2, 2)
    assert set(body) == {
        "id",
        "title",
        "author",
        "mine",
        "published_at",
        "criteria_count",
        "questions_count",
        "criteria",
        "questions",
    }
    assert set(body["questions"][0]) == {"text", "difficulty", "topic", "position"}
    assert set(body["criteria"][0]) == {"name", "max_points", "position"}

    questions = (
        await client.get(f"{API}/assignments/{assignment['id']}/questions", headers=owner)
    ).json()
    await client.patch(
        f"{API}/assignments/{assignment['id']}/questions/{questions[0]['id']}",
        headers=owner,
        json={"text": "Совсем другой вопрос"},
    )
    seen = (
        await client.get(f"{API}/spaces/{space['id']}/publications/{body['id']}", headers=member)
    ).json()
    assert seen["mine"] is False
    assert [q["text"] for q in seen["questions"]] == ["Снимок: вопрос 0", "Снимок: вопрос 1"]


@pytest.mark.asyncio
async def test_publishing_checks_ownership_membership_and_content(client, world):
    owner = await world.teacher("sp_pub_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_pub_member")
    stranger = await world.teacher("sp_pub_stranger")
    mine = await world.assignment(owner, "Моя")
    empty = await world.assignment(owner, "Пустая", questions=0, criteria=False)
    theirs = await world.assignment(member, "Чужая участника")

    others = await world.publish(owner, space, theirs)
    nothing = await world.publish(owner, space, empty)
    outsider = await world.publish(
        stranger, space, await world.assignment(stranger, "Постороннего")
    )
    ok = await world.publish(member, space, theirs)
    only_questions = await world.publish(
        owner, space, await world.assignment(owner, "Без критериев", criteria=False)
    )

    assert others.status_code == 404 and others.json()["code"] == "assignment_not_found"
    assert nothing.status_code == 422 and nothing.json()["code"] == "publication_empty"
    assert outsider.status_code == 404 and outsider.json()["code"] == "space_not_found"
    assert ok.status_code == 201 and only_questions.status_code == 201
    assert mine["id"] != ok.json()["id"]
    listed = (await client.get(f"{API}/spaces/{space['id']}/publications", headers=owner)).json()
    assert sorted(p["title"] for p in listed) == ["Без критериев", "Чужая участника"]
    assert {p["title"]: p["mine"] for p in listed}["Чужая участника"] is False


@pytest.mark.asyncio
async def test_a_copy_carries_attribution_and_is_independent_of_the_publication(client, world):
    owner = await world.teacher("sp_copy_owner")
    space = await world.space(owner, "Алгоритмы")
    reader = await world.member(owner, space, "sp_copy_reader")
    published = (
        await world.publish(owner, space, await world.assignment(owner, "Сортировки"))
    ).json()
    base = f"{API}/spaces/{space['id']}/publications/{published['id']}"

    r = await client.post(f"{base}/copy", headers=reader, json={})

    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["title"] == "Сортировки (копия)"
    assert clone["origin"] == {
        "author": "sp_copy_owner",
        "space": "Алгоритмы",
        "title": "Сортировки",
    }
    assert clone["question_count"] == 2 and clone["criteria_count"] == 2
    assert clone["submissions_count"] == 0 and clone["is_template"] is False
    mine = (await client.get(f"{API}/assignments", headers=reader)).json()
    assert [a["id"] for a in mine] == [clone["id"]]
    assert (await client.get(f"{API}/assignments/{clone['id']}", headers=owner)).status_code == 404

    questions = (
        await client.get(f"{API}/assignments/{clone['id']}/questions", headers=reader)
    ).json()
    edited = await client.patch(
        f"{API}/assignments/{clone['id']}/questions/{questions[0]['id']}",
        headers=reader,
        json={"text": "Мой вариант"},
    )
    assert edited.status_code == 200
    again = (await client.get(base, headers=owner)).json()
    assert [q["text"] for q in again["questions"]][0] == "Сортировки: вопрос 0"

    custom = await client.post(f"{base}/copy", headers=reader, json={"title": "Мои сортировки"})
    taken = await client.post(f"{base}/copy", headers=reader, json={"title": "Мои сортировки"})
    second = await client.post(f"{base}/copy", headers=reader, json={})
    assert custom.json()["title"] == "Мои сортировки"
    assert taken.status_code == 409 and taken.json()["code"] == "assignment_title_taken"
    assert second.json()["title"] == "Сортировки (копия 2)"
    plain = (await client.get(f"{API}/assignments", headers=reader)).json()
    assert [a for a in plain if a["origin"] is None] == []


@pytest.mark.asyncio
async def test_a_copy_respects_the_assignment_limit(client, world, monkeypatch):
    owner = await world.teacher("sp_copy_limit")
    space = await world.space(owner)
    published = (
        await world.publish(owner, space, await world.assignment(owner, "Исходник"))
    ).json()
    monkeypatch.setattr(settings, "MAX_ASSIGNMENTS_PER_TEACHER", 2)

    base = f"{API}/spaces/{space['id']}/publications/{published['id']}/copy"
    first = await client.post(base, headers=owner, json={})
    second = await client.post(base, headers=owner, json={})

    assert first.status_code == 201
    assert second.status_code == 409 and second.json()["code"] == "assignment_limit"


@pytest.mark.asyncio
async def test_only_the_author_can_unpublish_and_copies_survive_it(client, world):
    owner = await world.teacher("sp_unpub_owner")
    space = await world.space(owner)
    author = await world.member(owner, space, "sp_unpub_author")
    reader = await world.member(owner, space, "sp_unpub_reader")
    published = (
        await world.publish(author, space, await world.assignment(author, "Авторская"))
    ).json()
    base = f"{API}/spaces/{space['id']}/publications/{published['id']}"
    copy = (await client.post(f"{base}/copy", headers=reader, json={})).json()

    by_owner = await client.delete(base, headers=owner)
    by_reader = await client.delete(base, headers=reader)
    by_author = await client.delete(base, headers=author)
    again = await client.delete(base, headers=author)

    assert by_owner.status_code == 403 and by_owner.json()["code"] == "not_publication_author"
    assert by_reader.status_code == 403
    assert by_author.status_code == 204
    assert again.status_code == 404 and again.json()["code"] == "publication_not_found"
    assert (await client.get(base, headers=reader)).status_code == 404
    kept = (await client.get(f"{API}/assignments/{copy['id']}", headers=reader)).json()
    assert kept["question_count"] == 2 and kept["origin"]["author"] == "sp_unpub_author"


@pytest.mark.asyncio
async def test_others_materials_cannot_be_edited(client, world):
    owner = await world.teacher("sp_edit_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_edit_member")
    assignment = await world.assignment(owner, "Защищённая")
    published = (await world.publish(owner, space, assignment)).json()
    base = f"{API}/spaces/{space['id']}/publications/{published['id']}"

    for method in ("patch", "put"):
        r = await getattr(client, method)(base, headers=member, json={"title": "Взлом"})
        assert r.status_code == 405
    questions = (
        await client.get(f"{API}/assignments/{assignment['id']}/questions", headers=owner)
    ).json()
    probes = [
        client.patch(f"{API}/assignments/{assignment['id']}", headers=member, json={"title": "X"}),
        client.delete(f"{API}/assignments/{assignment['id']}", headers=member),
        client.post(
            f"{API}/assignments/{assignment['id']}/questions", headers=member, json={"text": "Z"}
        ),
        client.delete(
            f"{API}/assignments/{assignment['id']}/questions/{questions[0]['id']}", headers=member
        ),
    ]
    assert [r.status_code for r in await asyncio.gather(*probes)] == [404, 404, 404, 404]
    assert (await client.get(base, headers=member)).json()["title"] == "Защищённая"
    assert (await client.get(f"{API}/assignments/{assignment['id']}", headers=owner)).json()[
        "question_count"
    ] == 2


@pytest.mark.asyncio
async def test_members_never_see_students_scores_submissions_audit_or_analytics(
    client, world, make_session
):
    owner = await world.teacher("sp_iso_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_iso_member")
    assignment = await world.assignment(owner, "Рабочая")
    code = await make_session(owner)
    await client.put(
        f"{API}/sessions/{code}/assignment",
        headers=owner,
        json={"assignment_id": assignment["id"]},
    )
    await client.post(
        f"{API}/sessions/{code}/join",
        json={"name": "Секретный Студент", "token": "tok-iso-" + "x" * 12},
    )
    await client.post(f"{API}/sessions/{code}/next", headers=owner)
    current = (await client.get(f"{API}/sessions/{code}/submissions/current", headers=owner)).json()
    criteria = (
        await client.get(f"{API}/assignments/{assignment['id']}/criteria", headers=owner)
    ).json()
    done = await client.post(
        f"{API}/submissions/{current['id']}/complete",
        headers=owner,
        json={
            "scores": [{"criterion_id": c["id"], "points": 1} for c in criteria],
            "comment": "Секретный комментарий",
        },
    )
    assert done.status_code == 200, done.text
    published = await world.publish(owner, space, assignment)
    assert published.status_code == 201
    students = (await client.get(f"{API}/students", headers=owner)).json()
    assert [s["name"] for s in students] == ["Секретный Студент"]

    shown = (
        await client.get(
            f"{API}/spaces/{space['id']}/publications/{published.json()['id']}", headers=member
        )
    ).text
    detail = (await client.get(f"{API}/spaces/{space['id']}", headers=member)).text
    for payload in (shown, detail):
        assert "Секретный" not in payload and "Студент" not in payload
        assert "token" not in payload and assignment["id"] not in payload

    denied = [
        f"/assignments/{assignment['id']}",
        f"/assignments/{assignment['id']}/analytics",
        f"/sessions/{code}/analytics",
        f"/sessions/{code}/submissions",
        f"/submissions/{current['id']}",
        f"/submissions/{current['id']}/audit",
    ]
    for path in denied:
        r = await client.get(f"{API}{path}", headers=member)
        assert r.status_code == 404, path
    assert (await client.get(f"{API}/students", headers=member)).json() == []
    assert (
        await client.get(f"{API}/students/{students[0]['id']}", headers=member)
    ).status_code == 404


@pytest.mark.asyncio
async def test_a_member_can_leave_and_takes_their_publications_away(client, world, db_session):
    owner = await world.teacher("sp_leave_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_leave_member")
    published = (
        await world.publish(member, space, await world.assignment(member, "Уходящего"))
    ).json()
    copy = (
        await client.post(
            f"{API}/spaces/{space['id']}/publications/{published['id']}/copy",
            headers=owner,
            json={},
        )
    ).json()

    left = await client.delete(f"{API}/spaces/{space['id']}/members/me", headers=member)
    owner_left = await client.delete(f"{API}/spaces/{space['id']}/members/me", headers=owner)

    assert left.status_code == 204
    assert owner_left.status_code == 409 and owner_left.json()["code"] == "owner_cannot_leave"
    assert (await client.get(f"{API}/spaces/{space['id']}", headers=member)).status_code == 404
    assert (
        await client.get(f"{API}/spaces/{space['id']}/publications", headers=owner)
    ).json() == []
    assert (await client.get(f"{API}/assignments/{copy['id']}", headers=owner)).status_code == 200
    rows = (
        await db_session.execute(
            select(func.count())
            .select_from(PublicationORM)
            .where(PublicationORM.space_id == space["id"])
        )
    ).scalar_one()
    assert rows == 0


@pytest.mark.asyncio
async def test_the_owner_removes_members_and_nobody_else_can(client, world):
    owner = await world.teacher("sp_rm_owner")
    space = await world.space(owner)
    first = await world.member(owner, space, "sp_rm_first")
    second = await world.member(owner, space, "sp_rm_second")
    members = (await client.get(f"{API}/spaces/{space['id']}", headers=owner)).json()["members"]
    ids = {m["username"]: m["user_id"] for m in members}
    base = f"{API}/spaces/{space['id']}/members"

    by_member = await client.delete(f"{base}/{ids['sp_rm_second']}", headers=first)
    the_owner = await client.delete(f"{base}/{ids['sp_rm_owner']}", headers=owner)
    unknown = await client.delete(f"{base}/no-such-user", headers=owner)
    removed = await client.delete(f"{base}/{ids['sp_rm_first']}", headers=owner)

    assert by_member.status_code == 403 and by_member.json()["code"] == "not_space_owner"
    assert the_owner.status_code == 409
    assert unknown.status_code == 404 and unknown.json()["code"] == "member_not_found"
    assert removed.status_code == 204
    assert (await client.get(f"{API}/spaces/{space['id']}", headers=first)).status_code == 404
    assert (await client.get(f"{API}/spaces/{space['id']}", headers=second)).status_code == 200
    rejoin = await world.join(first, space["invite_code"])
    assert rejoin.status_code == 200


@pytest.mark.asyncio
async def test_deleting_a_space_is_for_the_owner_and_keeps_copies(client, world, db_session):
    owner = await world.teacher("sp_del_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_del_member")
    published = (await world.publish(owner, space, await world.assignment(owner, "Общая"))).json()
    copy = (
        await client.post(
            f"{API}/spaces/{space['id']}/publications/{published['id']}/copy",
            headers=member,
            json={},
        )
    ).json()

    refused = await client.delete(f"{API}/spaces/{space['id']}", headers=member)
    deleted = await client.delete(f"{API}/spaces/{space['id']}", headers=owner)

    assert refused.status_code == 403 and refused.json()["code"] == "not_space_owner"
    assert deleted.status_code == 204
    assert (await client.get(f"{API}/spaces/{space['id']}", headers=member)).status_code == 404
    assert (await client.get(f"{API}/spaces", headers=member)).json() == []
    kept = (await client.get(f"{API}/assignments/{copy['id']}", headers=member)).json()
    assert kept["question_count"] == 2 and kept["origin"]["space"] == "Базы данных"
    left = (
        await db_session.execute(
            select(func.count())
            .select_from(SpaceMemberORM)
            .where(SpaceMemberORM.space_id == space["id"])
        )
    ).scalar_one()
    assert left == 0


@pytest.mark.asyncio
async def test_a_regenerated_invite_code_replaces_the_old_one(client, world):
    owner = await world.teacher("sp_inv_owner")
    space = await world.space(owner)
    member = await world.member(owner, space, "sp_inv_member")
    late = await world.teacher("sp_inv_late")

    refused = await client.post(f"{API}/spaces/{space['id']}/invite", headers=member)
    fresh = (await client.post(f"{API}/spaces/{space['id']}/invite", headers=owner)).json()

    assert refused.status_code == 403
    assert fresh["invite_code"] != space["invite_code"]
    assert (await world.join(late, space["invite_code"])).status_code == 404
    assert (await world.join(late, fresh["invite_code"])).status_code == 200


@pytest.mark.asyncio
async def test_space_limits_are_enforced_with_russian_messages(client, world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPACES_OWNED", 2)
    monkeypatch.setattr(settings, "MAX_SPACE_MEMBERS", 3)
    monkeypatch.setattr(settings, "MAX_SPACES_JOINED", 3)
    owner = await world.teacher("sp_lim_owner")
    first = await world.space(owner, "Первая")
    await world.space(owner, "Вторая")

    third = await client.post(f"{API}/spaces", headers=owner, json={"name": "Третья"})
    await world.member(owner, first, "sp_lim_a")
    await world.member(owner, first, "sp_lim_b")
    extra = await world.teacher("sp_lim_extra")
    full = await world.join(extra, first["invite_code"])

    assert third.status_code == 409 and third.json()["code"] == "space_limit"
    assert third.json()["detail"] == "Достигнут предел числа ваших групп"
    assert full.status_code == 409 and full.json()["code"] == "space_full"
    assert full.json()["detail"] == "В группе нет свободных мест"
    assert (await client.get(f"{API}/spaces/{first['id']}", headers=owner)).json()[
        "members_count"
    ] == 3


@pytest.mark.asyncio
async def test_joined_spaces_limit(client, world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPACES_JOINED", 2)
    guest = await world.teacher("sp_joined_guest")
    codes = []
    for i in range(3):
        owner = await world.teacher(f"sp_joined_owner{i}")
        codes.append((await world.space(owner, f"Группа {i}"))["invite_code"])

    results = [(await world.join(guest, code)).status_code for code in codes]

    assert results == [200, 200, 409]


@pytest.mark.asyncio
async def test_publication_limits(client, world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_PUBLICATIONS_PER_AUTHOR", 2)
    monkeypatch.setattr(settings, "MAX_SPACE_PUBLICATIONS", 3)
    owner = await world.teacher("sp_pubs_owner")
    space = await world.space(owner)
    other = await world.member(owner, space, "sp_pubs_other")
    mine = await world.assignment(owner, "Моя")
    theirs = await world.assignment(other, "Их")

    codes = [
        (await world.publish(owner, space, mine, title="Раз")).status_code,
        (await world.publish(owner, space, mine, title="Два")).status_code,
        (await world.publish(owner, space, mine, title="Три")).status_code,
        (await world.publish(other, space, theirs, title="Раз их")).status_code,
        (await world.publish(other, space, theirs, title="Два их")).status_code,
    ]

    assert codes == [201, 201, 409, 201, 409]
    over = await world.publish(other, space, theirs, title="Ещё")
    assert over.json()["code"] == "publication_limit"


@pytest.mark.asyncio
async def test_parallel_joins_cannot_exceed_the_member_limit(client, world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPACE_MEMBERS", 4)
    owner = await world.teacher("sp_race_owner")
    space = await world.space(owner)
    guests = [await world.teacher(f"sp_race_guest{i}") for i in range(8)]

    results = await asyncio.gather(*[world.join(g, space["invite_code"]) for g in guests])

    assert sorted(r.status_code for r in results) == [200] * 3 + [409] * 5
    assert (await client.get(f"{API}/spaces/{space['id']}", headers=owner)).json()[
        "members_count"
    ] == 4


@pytest.mark.asyncio
async def test_parallel_creates_cannot_exceed_the_owned_limit(client, world, monkeypatch):
    monkeypatch.setattr(settings, "MAX_SPACES_OWNED", 3)
    owner = await world.teacher("sp_race_create")

    results = await asyncio.gather(
        *[
            client.post(f"{API}/spaces", headers=owner, json={"name": f"Группа {i}"})
            for i in range(7)
        ]
    )

    assert sorted(r.status_code for r in results) == [201] * 3 + [409] * 4
    assert len((await client.get(f"{API}/spaces", headers=owner)).json()) == 3


@pytest.mark.asyncio
async def test_removing_a_member_racing_their_publish_leaves_no_orphan_material(
    client, world, db_session
):
    for round_number in range(4):
        owner = await world.teacher(f"sp_rr_owner{round_number}")
        space = await world.space(owner)
        member = await world.member(owner, space, f"sp_rr_member{round_number}")
        assignment = await world.assignment(member, "Гонка")
        members = (await client.get(f"{API}/spaces/{space['id']}", headers=owner)).json()["members"]
        member_id = next(m["user_id"] for m in members if not m["is_owner"])

        removal, publication = await asyncio.gather(
            client.delete(f"{API}/spaces/{space['id']}/members/{member_id}", headers=owner),
            world.publish(member, space, assignment),
        )

        assert removal.status_code == 204
        assert publication.status_code in {201, 404}
        left = (
            await db_session.execute(
                select(func.count())
                .select_from(PublicationORM)
                .where(PublicationORM.space_id == space["id"])
            )
        ).scalar_one()
        assert left == 0


@pytest.mark.asyncio
async def test_unpublishing_racing_a_copy_gives_a_whole_copy_or_not_found(client, world):
    owner = await world.teacher("sp_ru_owner")
    space = await world.space(owner)
    reader = await world.member(owner, space, "sp_ru_reader")
    published = (await world.publish(owner, space, await world.assignment(owner, "Гонка"))).json()
    base = f"{API}/spaces/{space['id']}/publications/{published['id']}"

    copy, removal = await asyncio.gather(
        client.post(f"{base}/copy", headers=reader, json={}),
        client.delete(base, headers=owner),
    )

    assert removal.status_code == 204
    assert copy.status_code in {201, 404}
    if copy.status_code == 201:
        assert copy.json()["question_count"] == 2 and copy.json()["criteria_count"] == 2


@pytest.mark.asyncio
async def test_parallel_copies_of_one_publication_get_distinct_titles(client, world):
    owner = await world.teacher("sp_pc_owner")
    space = await world.space(owner)
    published = (await world.publish(owner, space, await world.assignment(owner, "Много"))).json()
    base = f"{API}/spaces/{space['id']}/publications/{published['id']}/copy"

    results = await asyncio.gather(*[client.post(base, headers=owner, json={}) for _ in range(6)])

    assert [r.status_code for r in results] == [201] * 6
    assert len({r.json()["title"] for r in results}) == 6
