import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

pytest.importorskip("playwright")

from sqlalchemy import text  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402
from sqlalchemy.pool import NullPool  # noqa: E402

from core.config import settings  # noqa: E402
from tests.conftest import TEST_DATABASE_URL  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SHOTS = ROOT / "docs" / "screenshots"
E2E_DB = "queue_e2e"
E2E_URL = TEST_DATABASE_URL.set(database=E2E_DB)


async def _recreate_database(create: bool) -> None:
    admin = create_async_engine(
        TEST_DATABASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    async with admin.connect() as conn:
        await conn.execute(text(f'DROP DATABASE IF EXISTS "{E2E_DB}" WITH (FORCE)'))
        if create:
            await conn.execute(text(f'CREATE DATABASE "{E2E_DB}"'))
    await admin.dispose()


def _run(coro_factory) -> None:
    thread = threading.Thread(target=lambda: asyncio.run(coro_factory()))
    thread.start()
    thread.join()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SERVERS: list[subprocess.Popen] = []


@pytest.fixture(autouse=True)
def server_stays_up():
    yield
    for server in SERVERS:
        if server.poll() is not None:
            pytest.fail(f"the e2e server exited with code {server.returncode}", pytrace=False)


@pytest.fixture(scope="session")
def live_server():
    _run(lambda: _recreate_database(create=True))
    env = {
        **os.environ,
        "DATABASE_URL": E2E_URL.render_as_string(hide_password=False),
        "LOG_LEVEL": "WARNING",
        "RATE_LIMIT_AUTH_PER_IP_PER_MINUTE": "100000",
        "SSE_HEARTBEAT_SECONDS": "1",
        "PYTHONFAULTHANDLER": "1",
        "AI_ENABLED": "true",
        "OLLAMA_URL": "http://127.0.0.1:9",
        "AI_TIMEOUT_SECONDS": "5",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
    )
    port = _free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "main:app",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        cwd=ROOT,
        env=env,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.2)
        else:
            raise RuntimeError("e2e server did not start")
        SERVERS.append(server)
        yield url
    finally:
        SERVERS.clear()
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        _run(lambda: _recreate_database(create=False))


@pytest.fixture(scope="session")
def base_url(live_server):
    return live_server


@pytest.fixture(scope="session")
def api(live_server):
    with httpx.Client(base_url=f"{live_server}/api/v1", timeout=10) as client:
        yield client


@pytest.fixture
def teacher_api(api):
    def _make() -> dict:
        username = f"e2e_{time.time_ns()}"
        password = "password123"
        response = api.post(
            "/auth/register",
            json={
                "username": username,
                "password": password,
                "invite_code": settings.TEACHER_INVITE_CODE,
            },
        )
        assert response.status_code == 201, response.text
        login = api.post("/auth/login", json={"username": username, "password": password})
        token = login.json()["access_token"]
        return {
            "username": username,
            "password": password,
            "headers": {"Authorization": f"Bearer {token}"},
        }

    return _make


@pytest.fixture
def open_session(api, teacher_api):
    def _open(title: str = "Лабораторная 1", **extra) -> dict:
        teacher = teacher_api()
        response = api.post("/sessions", json={"title": title, **extra}, headers=teacher["headers"])
        assert response.status_code == 201, response.text
        return {**teacher, "code": response.json()["code"]}

    return _open


@pytest.fixture
def shot():
    SHOTS.mkdir(parents=True, exist_ok=True)

    def _shot(page, name: str) -> None:
        page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)

    return _shot


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/e2e" in item.nodeid.replace("\\", "/"):
            item.add_marker(pytest.mark.e2e)
