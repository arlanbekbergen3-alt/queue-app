import asyncio

import pytest

from tests.test_protocol import API, call_next, full_scores


async def completed(client, setup, name, **kwargs):
    ctx = await setup(name, **kwargs)
    sub = await call_next(client, ctx)
    await client.post(
        f"{API}/submissions/{sub['id']}/complete",
        headers=ctx["headers"],
        json=full_scores(ctx, 2),
    )
    return ctx, sub, ctx["criteria"][0]["id"]


def body(cid, points, reason="Пересмотр"):
    return {"scores": [{"criterion_id": cid, "points": points}], "reason": reason}


async def correct_entries(client, ctx, sub):
    audit = (
        await client.get(f"{API}/submissions/{sub['id']}/audit", headers=ctx["headers"])
    ).json()
    return [a for a in audit if a["action"] == "correct"]


@pytest.mark.asyncio
async def test_a_repeated_request_with_the_same_key_returns_the_result_without_a_new_entry(
    client, setup
):
    ctx, sub, cid = await completed(client, setup, "idem_repeat")
    headers = {**ctx["headers"], "Idempotency-Key": "key-0001-abcdef"}
    url = f"{API}/submissions/{sub['id']}/correct"

    first = await client.post(url, headers=headers, json=body(cid, 4))
    second = await client.post(url, headers=headers, json=body(cid, 4))

    assert first.status_code == 200 and second.status_code == 200
    assert second.json()["total_points"] == first.json()["total_points"] == 6
    assert len(await correct_entries(client, ctx, sub)) == 1


@pytest.mark.asyncio
async def test_without_a_key_the_same_data_is_still_refused_as_nothing_to_correct(client, setup):
    ctx, sub, cid = await completed(client, setup, "idem_nokey")
    url = f"{API}/submissions/{sub['id']}/correct"

    first = await client.post(url, headers=ctx["headers"], json=body(cid, 4))
    second = await client.post(url, headers=ctx["headers"], json=body(cid, 4))

    assert first.status_code == 200
    assert (second.status_code, second.json()["code"]) == (409, "nothing_to_correct")
    assert len(await correct_entries(client, ctx, sub)) == 1


@pytest.mark.asyncio
async def test_parallel_sends_with_one_key_write_exactly_one_entry(client, setup):
    ctx, sub, cid = await completed(client, setup, "idem_parallel")
    headers = {**ctx["headers"], "Idempotency-Key": "key-0002-abcdef"}
    url = f"{API}/submissions/{sub['id']}/correct"

    responses = await asyncio.gather(
        *(client.post(url, headers=headers, json=body(cid, 4)) for _ in range(12))
    )

    assert {r.status_code for r in responses} == {200}
    assert {r.json()["total_points"] for r in responses} == {6}
    assert len(await correct_entries(client, ctx, sub)) == 1


@pytest.mark.asyncio
async def test_a_new_key_is_a_new_correction(client, setup):
    ctx, sub, cid = await completed(client, setup, "idem_newkey")
    url = f"{API}/submissions/{sub['id']}/correct"

    first = await client.post(
        url, headers={**ctx["headers"], "Idempotency-Key": "key-0003-aaaaaa"}, json=body(cid, 4)
    )
    second = await client.post(
        url, headers={**ctx["headers"], "Idempotency-Key": "key-0003-bbbbbb"}, json=body(cid, 5)
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert second.json()["total_points"] == 7
    assert len(await correct_entries(client, ctx, sub)) == 2


@pytest.mark.asyncio
async def test_a_key_is_scoped_to_its_submission(client, setup):
    ctx, first_sub, cid = await completed(client, setup, "idem_scope", students=2)
    second_sub = await call_next(client, ctx)
    await client.post(
        f"{API}/submissions/{second_sub['id']}/complete",
        headers=ctx["headers"],
        json=full_scores(ctx, 2),
    )
    headers = {**ctx["headers"], "Idempotency-Key": "key-0004-abcdef"}

    one = await client.post(
        f"{API}/submissions/{first_sub['id']}/correct", headers=headers, json=body(cid, 4)
    )
    two = await client.post(
        f"{API}/submissions/{second_sub['id']}/correct", headers=headers, json=body(cid, 4)
    )

    assert (one.status_code, two.status_code) == (200, 200)
    assert len(await correct_entries(client, ctx, first_sub)) == 1
    assert len(await correct_entries(client, ctx, second_sub)) == 1


@pytest.mark.asyncio
async def test_a_malformed_key_is_rejected_and_the_endpoint_still_needs_the_owner(
    client, setup, make_teacher
):
    ctx, sub, cid = await completed(client, setup, "idem_bad")
    url = f"{API}/submissions/{sub['id']}/correct"

    short = await client.post(
        url, headers={**ctx["headers"], "Idempotency-Key": "x"}, json=body(cid, 4)
    )
    stranger = await make_teacher("idem_stranger")
    foreign = await client.post(
        url, headers={**stranger, "Idempotency-Key": "key-0005-abcdef"}, json=body(cid, 4)
    )

    assert short.status_code == 422
    assert foreign.status_code in (403, 404)
    assert await correct_entries(client, ctx, sub) == []
