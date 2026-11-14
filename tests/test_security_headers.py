import base64
import hashlib
import re
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from httpx import ASGITransport, AsyncClient

from core import security_headers
from core.attempts import AttemptGuard
from core.config import settings
from core.security_headers import SecurityHeadersMiddleware, build_csp, script_hashes
from services.auth import login_guard

STATIC = Path(__file__).resolve().parent.parent / "static"
PAGES = ("/", "/teacher", "/protocol", "/students", "/spaces", "/practice")
API = "/api/v1"


def directive(csp: str, name: str) -> str:
    return next(part for part in csp.split("; ") if part.startswith(name))


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_every_page_carries_a_strict_content_security_policy(client, path):
    response = await client.get(path)
    csp = response.headers["content-security-policy"]

    assert response.status_code == 200
    assert "'unsafe-inline'" not in directive(csp, "script-src")
    assert "'unsafe-eval'" not in csp
    assert directive(csp, "frame-ancestors") == "frame-ancestors 'none'"
    assert directive(csp, "object-src") == "object-src 'none'"
    assert directive(csp, "connect-src") == "connect-src 'self'"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", PAGES)
async def test_the_inline_script_of_every_page_is_allowed_by_its_hash(client, path):
    response = await client.get(path)
    csp = response.headers["content-security-policy"]
    text = response.content.decode("utf-8").replace("\r\n", "\n")
    bodies = re.findall(r"<script>(.*?)</script>", text, re.DOTALL)

    assert bodies
    for body in bodies:
        digest = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
        assert f"'sha256-{digest}'" in directive(csp, "script-src")


def test_a_changed_script_gets_a_different_hash(tmp_path):
    page = tmp_path / "page.html"
    page.write_text("<html><script>run()</script></html>", encoding="utf-8")
    before = script_hashes(tmp_path)

    page.write_text("<html><script>run(); steal()</script></html>", encoding="utf-8")
    after = script_hashes(tmp_path)

    assert len(before) == 1 and len(after) == 1 and before != after


def test_line_endings_do_not_change_the_hash(tmp_path):
    (tmp_path / "unix.html").write_bytes(b"<script>a\nb\n</script>")
    unix = script_hashes(tmp_path)
    (tmp_path / "unix.html").unlink()
    (tmp_path / "win.html").write_bytes(b"<script>a\r\nb\r\n</script>")

    assert script_hashes(tmp_path) == unix


def test_the_policy_lists_only_the_given_hashes():
    csp = build_csp(["'sha256-AAA'", "'sha256-BBB'"])

    assert directive(csp, "script-src") == "script-src 'self' 'sha256-AAA' 'sha256-BBB'"


@pytest.mark.asyncio
async def test_api_answers_get_the_basic_protective_headers(client):
    response = await client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "camera=()" in response.headers["permissions-policy"]
    assert response.headers["cross-origin-opener-policy"] == "same-origin"
    assert "content-security-policy" not in response.headers
    assert "strict-transport-security" not in response.headers


@pytest.mark.asyncio
async def test_error_answers_are_protected_too(client):
    missing = await client.get(f"{API}/sessions/NOSUCH")
    unauthorized = await client.get(f"{API}/auth/me")

    for response in (missing, unauthorized):
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_the_interactive_api_docs_keep_working_without_the_page_policy(client):
    response = await client.get("/docs")

    assert response.status_code == 200
    assert "content-security-policy" not in response.headers
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.asyncio
async def test_transport_security_is_sent_only_when_enabled(monkeypatch, tmp_path):
    app = FastAPI()

    @app.get("/")
    async def index():
        return HTMLResponse("<p>ok</p>")

    async def fetch(max_age: int):
        monkeypatch.setattr(settings, "HSTS_MAX_AGE_SECONDS", max_age)
        wrapped = SecurityHeadersMiddleware(app, static_dir=tmp_path)
        async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as c:
            return await c.get("/")

    off = await fetch(0)
    on = await fetch(31536000)

    assert "strict-transport-security" not in off.headers
    assert on.headers["strict-transport-security"] == "max-age=31536000"
    assert "content-security-policy" in on.headers


@pytest.mark.asyncio
async def test_existing_headers_of_a_response_are_not_overwritten(tmp_path):
    app = FastAPI()

    @app.get("/")
    async def index():
        return HTMLResponse("<p>ok</p>", headers={"X-Frame-Options": "SAMEORIGIN"})

    wrapped = SecurityHeadersMiddleware(app, static_dir=tmp_path)
    async with AsyncClient(transport=ASGITransport(app=wrapped), base_url="http://t") as c:
        response = await c.get("/")

    assert response.headers["x-frame-options"] == "SAMEORIGIN"


def test_every_real_page_has_exactly_one_hashed_script():
    hashes = script_hashes(STATIC)

    assert len(hashes) == len(list(STATIC.glob("*.html")))
    assert security_headers.SKIP_CSP_PATHS == ("/docs", "/redoc", "/openapi.json")


@pytest.fixture
def registered(make_teacher):
    async def _registered(name: str):
        await make_teacher(name)
        return name

    return _registered


async def sign_in(client, name: str, password: str):
    return await client.post(f"{API}/auth/login", json={"username": name, "password": password})


@pytest.mark.asyncio
async def test_repeated_wrong_passwords_lock_the_login_even_for_the_right_one(client, registered):
    name = await registered("lock_one")
    limit = settings.LOGIN_FAILURES_PER_IP

    wrong = [await sign_in(client, name, "bad-password") for _ in range(limit)]
    locked = await sign_in(client, name, "password123")

    assert [r.status_code for r in wrong] == [401] * limit
    assert locked.status_code == 429 and locked.json()["code"] == "login_locked"
    assert int(locked.headers["retry-after"]) > 0


@pytest.mark.asyncio
async def test_a_lock_on_one_account_does_not_stop_another(client, registered):
    first = await registered("lock_a")
    second = await registered("lock_b")
    for _ in range(settings.LOGIN_FAILURES_PER_IP):
        await sign_in(client, first, "bad-password")

    other = await sign_in(client, second, "password123")

    assert other.status_code == 200


@pytest.mark.asyncio
async def test_a_good_login_clears_the_failures_so_they_do_not_add_up(client, registered):
    name = await registered("lock_clear")
    almost = settings.LOGIN_FAILURES_PER_IP - 1
    for _ in range(almost):
        await sign_in(client, name, "bad-password")
    ok = await sign_in(client, name, "password123")
    for _ in range(almost):
        await sign_in(client, name, "bad-password")
    still_ok = await sign_in(client, name, "password123")

    assert ok.status_code == 200 and still_ok.status_code == 200


@pytest.mark.asyncio
async def test_unknown_names_are_locked_the_same_way_so_existence_is_not_revealed(client):
    limit = settings.LOGIN_FAILURES_PER_IP
    answers = [await sign_in(client, "nobody_here", "whatever1") for _ in range(limit + 1)]

    assert [a.status_code for a in answers] == [401] * limit + [429]


@pytest.mark.asyncio
async def test_a_password_spray_from_many_addresses_locks_the_account_for_everyone(
    client, registered
):
    name = await registered("lock_spray")
    for i in range(settings.LOGIN_FAILURES_PER_USER):
        login_guard.failed(name.casefold(), f"10.0.{i // 250}.{i % 250}")

    answer = await sign_in(client, name, "password123")

    assert answer.status_code == 429 and answer.json()["code"] == "login_locked"


@pytest.mark.asyncio
async def test_the_name_is_matched_without_regard_to_case(client, registered):
    name = await registered("lock_case")
    for i in range(settings.LOGIN_FAILURES_PER_IP):
        await sign_in(client, name.upper() if i % 2 else name, "bad-password")

    answer = await sign_in(client, name, "password123")

    assert answer.status_code == 429


def test_the_lock_expires_and_failures_are_forgotten_with_time():
    now = [0.0]
    guard = AttemptGuard(
        ip_failures=2,
        session_failures=10,
        window_seconds=60,
        lock_seconds=100,
        clock=lambda: now[0],
    )
    guard.failed("u", "1.1.1.1")
    guard.failed("u", "1.1.1.1")
    assert guard.retry_after("u", "1.1.1.1") > 0

    now[0] = 200.0

    assert guard.retry_after("u", "1.1.1.1") == 0
    guard.failed("u", "1.1.1.1")
    now[0] = 300.0
    guard.failed("u", "1.1.1.1")
    assert guard.retry_after("u", "1.1.1.1") == 0


def test_clearing_one_address_leaves_the_others_counting():
    guard = AttemptGuard(ip_failures=3, session_failures=10)
    for _ in range(2):
        guard.failed("u", "1.1.1.1")
        guard.failed("u", "2.2.2.2")

    guard.clear("u", "1.1.1.1")
    guard.failed("u", "1.1.1.1")
    guard.failed("u", "2.2.2.2")

    assert guard.retry_after("u", "1.1.1.1") == 0
    assert guard.retry_after("u", "2.2.2.2") > 0
