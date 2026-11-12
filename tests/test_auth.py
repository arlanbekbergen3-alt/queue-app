import pytest

from core.config import settings

AUTH = "/api/v1/auth"


def payload(username="teacher_a", **overrides):
    return {
        "username": username,
        "password": "password123",
        "invite_code": settings.TEACHER_INVITE_CODE,
        **overrides,
    }


@pytest.mark.asyncio
async def test_register_requires_valid_invite_code(client):
    response = await client.post(f"{AUTH}/register", json=payload("inv_bad", invite_code="nope"))
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_register_login_and_me(client):
    assert (await client.post(f"{AUTH}/register", json=payload("auth_ok"))).status_code == 201
    login = await client.post(
        f"{AUTH}/login", json={"username": "auth_ok", "password": "password123"}
    )
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    me = await client.get(f"{AUTH}/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["username"] == "auth_ok"
    assert "password_hash" not in me.json()


@pytest.mark.asyncio
async def test_duplicate_username_conflicts(client):
    await client.post(f"{AUTH}/register", json=payload("auth_dup"))
    again = await client.post(f"{AUTH}/register", json=payload("auth_dup"))
    assert again.status_code == 409


@pytest.mark.asyncio
async def test_wrong_credentials_are_rejected(client):
    await client.post(f"{AUTH}/register", json=payload("auth_wrong"))
    bad_password = await client.post(
        f"{AUTH}/login", json={"username": "auth_wrong", "password": "WRONG-password"}
    )
    unknown_user = await client.post(
        f"{AUTH}/login", json={"username": "ghost_user", "password": "password123"}
    )
    assert bad_password.status_code == unknown_user.status_code == 401
    same = lambda r: {k: v for k, v in r.json().items() if k != "request_id"}  # noqa: E731
    assert same(bad_password) == same(unknown_user)
    assert same(bad_password)["code"] == "invalid_credentials"


@pytest.mark.asyncio
async def test_protected_routes_need_a_valid_token(client):
    assert (await client.get(f"{AUTH}/me")).status_code == 401
    bad = await client.get(f"{AUTH}/me", headers={"Authorization": "Bearer garbage"})
    assert bad.status_code == 401
    assert (await client.post("/api/v1/sessions", json={"title": "x"})).status_code == 401


@pytest.mark.asyncio
async def test_short_password_is_rejected(client):
    response = await client.post(f"{AUTH}/register", json=payload("auth_short", password="123"))
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_register_validation_errors_name_the_field_and_the_limit(client):
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": "anna@example.com", "password": "short", "invite_code": "x"},
    )

    assert response.status_code == 422
    by_loc = {e["loc"]: e for e in response.json()["errors"]}
    assert by_loc["body.username"]["type"] == "string_pattern_mismatch"
    assert by_loc["body.password"]["type"] == "string_too_short"
    assert by_loc["body.password"]["ctx"] == {"min_length": 8}
