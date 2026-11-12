import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import text, update
from sqlalchemy.exc import OperationalError

from core.config import Settings, settings
from core.events import add_listener
from core.logging import JsonFormatter, request_id_var
from main import app, lifespan
from models.entry import EntryORM
from models.queue_session import QueueSessionORM
from services.live import Subscriber, _SessionState, hub
from services.maintenance import close_idle_sessions, reaper_loop
from services.queue import QueueService
from tests.conftest import TestingSessionLocal

API = "/api/v1/sessions"

VALID = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@localhost:5432/db",
    "JWT_SECRET_KEY": "k" * 40,
    "TEACHER_INVITE_CODE": "invite-code-123",
}


def build(**overrides) -> Settings:
    return Settings(_env_file=None, **{**VALID, **overrides})  # type: ignore[call-arg]


@pytest.mark.asyncio
async def test_health_does_not_touch_the_database_and_ready_does(client):
    assert (await client.get("/health")).json() == {"status": "ok"}
    assert (await client.get("/ready")).json() == {"status": "ready"}


@pytest.mark.asyncio
async def test_ready_reports_an_outdated_schema(client, db_session):
    await db_session.execute(text("UPDATE alembic_version SET version_num = 'old'"))
    await db_session.commit()
    try:
        response = await client.get("/ready")
    finally:
        from core.readiness import expected_revision

        await db_session.execute(
            text("UPDATE alembic_version SET version_num = :rev"), {"rev": expected_revision()}
        )
        await db_session.commit()

    assert response.status_code == 503
    assert response.json()["code"] == "schema_outdated"
    assert (await client.get("/ready")).status_code == 200


@pytest.mark.asyncio
async def test_request_id_is_echoed_sanitized_or_generated(client):
    echoed = await client.get("/health", headers={"X-Request-ID": "trace-123.abc"})
    dirty = await client.get("/health", headers={"X-Request-ID": "a b<script>c"})
    generated = await client.get("/health")

    assert echoed.headers["X-Request-ID"] == "trace-123.abc"
    assert dirty.headers["X-Request-ID"] == "abscriptc"
    assert len(generated.headers["X-Request-ID"]) == 32


@pytest.mark.asyncio
async def test_every_error_has_detail_code_and_the_same_request_id(client, make_teacher):
    headers = await make_teacher("ops_errors")
    cases = {
        "not_found": await client.get("/api/v1/nothing", headers={"X-Request-ID": "r1"}),
        "method_not_allowed": await client.delete("/health", headers={"X-Request-ID": "r2"}),
        "validation_error": await client.post(
            API, headers={**headers, "X-Request-ID": "r3"}, json={"title": ""}
        ),
        "not_authenticated": await client.get(API, headers={"X-Request-ID": "r4"}),
        "session_not_found": await client.get(f"{API}/ZZZZZZ", headers={"X-Request-ID": "r5"}),
    }

    for code, response in cases.items():
        body = response.json()
        assert body["code"] == code, (code, body)
        assert isinstance(body["detail"], str) and body["detail"]
        assert body["request_id"] == response.headers["X-Request-ID"]
    errors = cases["validation_error"].json()["errors"]
    assert errors[0]["loc"] == "body.title" and errors[0]["msg"]


@pytest.mark.asyncio
async def test_unhandled_exception_returns_a_json_500_with_request_id(client, monkeypatch):
    async def boom(self, code):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(QueueService, "public_info", boom)

    response = await client.get(f"{API}/ABCDEF", headers={"X-Request-ID": "crash-1"})

    assert response.status_code == 500
    body = response.json()
    assert (body["code"], body["request_id"]) == ("internal_error", "crash-1")
    assert "secret" not in response.text


@pytest.mark.asyncio
async def test_database_connection_errors_become_503_with_retry_after(client, monkeypatch):
    async def down(self, code):
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(QueueService, "public_info", down)

    response = await client.get(f"{API}/ABCDEF")

    assert response.status_code == 503
    assert response.json()["code"] == "database_unavailable"
    assert int(response.headers["Retry-After"]) >= 1
    assert "connection refused" not in response.text


def test_json_log_lines_carry_the_request_id():
    token = request_id_var.set("log-req-7")
    try:
        record = logging.LogRecord(
            "queue.access", logging.INFO, __file__, 1, "GET / -> 200", (), None
        )
        from core.logging import RequestIdFilter

        RequestIdFilter().filter(record)
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id_var.reset(token)

    assert line["request_id"] == "log-req-7"
    assert line["message"] == "GET / -> 200"
    assert line["level"] == "INFO"


@pytest.mark.asyncio
async def test_metrics_endpoint_counts_requests_and_exposes_gauges(client):
    await client.get("/health")
    await client.get("/api/v1/nothing")

    text_ = (await client.get("/metrics")).text

    assert 'queue_http_requests_total{status="2xx"}' in text_
    assert 'queue_http_requests_total{status="4xx"}' in text_
    assert "queue_http_request_duration_seconds_count" in text_
    for name in (
        "queue_http_requests_in_flight",
        "queue_sse_connections",
        "queue_db_pool_checked_out",
        "queue_http_rejected_rate_limit_total",
        "queue_http_rejected_overload_total",
    ):
        assert name in text_, name


@pytest.mark.asyncio
async def test_metrics_can_be_protected_by_a_token(client, monkeypatch):
    monkeypatch.setattr(settings, "METRICS_TOKEN", "s3cret-metrics")

    assert (await client.get("/metrics")).status_code == 401
    assert (
        await client.get("/metrics", headers={"Authorization": "Bearer wrong"})
    ).status_code == 401
    ok = await client.get("/metrics", headers={"Authorization": "Bearer s3cret-metrics"})
    assert ok.status_code == 200


def test_valid_settings_load_with_safe_defaults():
    config = build()

    assert config.DB_POOL_TIMEOUT_SECONDS == 10
    assert config.SESSION_AUTO_CLOSE_HOURS == 6


@pytest.mark.parametrize(
    "overrides",
    [
        {"JWT_SECRET_KEY": "short"},
        {"JWT_SECRET_KEY": "change-me" + "x" * 30},
        {"TEACHER_INVITE_CODE": "change-me"},
        {"TEACHER_INVITE_CODE": "abc"},
        {"DATABASE_URL": "postgresql://u:p@localhost/db"},
        {"MAX_QUEUE_SIZE": 0},
        {"MAX_QUEUE_SIZE": 500, "MAX_ENTRIES_PER_SESSION": 100},
        {"NAME_MAX_LENGTH": 61},
        {"DB_POOL_SIZE": 0},
        {"SSE_HEARTBEAT_SECONDS": 0},
        {"LOG_FORMAT": "xml"},
        {"LOG_LEVEL": "LOUD"},
        {"RATE_LIMIT_IP_PER_MINUTE": -1},
    ],
)
def test_invalid_settings_stop_the_application_at_startup(overrides):
    with pytest.raises(ValidationError):
        build(**overrides)


def test_missing_required_settings_are_reported():
    with pytest.raises(ValidationError) as caught:
        Settings(_env_file=None)  # type: ignore[call-arg]
    missing = {e["loc"][0] for e in caught.value.errors()}
    assert {"DATABASE_URL", "JWT_SECRET_KEY", "TEACHER_INVITE_CODE"} <= missing


async def old_session(db_session, code: str, *, hours_ago: float, entry_hours_ago=None):
    long_ago = datetime.now(UTC) - timedelta(hours=hours_ago)
    await db_session.execute(
        update(QueueSessionORM).where(QueueSessionORM.code == code).values(created_at=long_ago)
    )
    if entry_hours_ago is not None:
        await db_session.execute(
            update(EntryORM)
            .where(EntryORM.session_id == QueueSessionORM.id, QueueSessionORM.code == code)
            .values(joined_at=datetime.now(UTC) - timedelta(hours=entry_hours_ago))
        )
    await db_session.commit()


async def status_of(client, code):
    return (await client.get(f"{API}/{code}")).json()["status"]


@pytest.mark.asyncio
async def test_forgotten_sessions_are_closed_but_active_ones_are_not(
    client, make_teacher, make_session, join, db_session
):
    headers = await make_teacher("ops_reaper")
    forgotten = await make_session(headers, "forgotten")
    busy = await make_session(headers, "busy")
    fresh = await make_session(headers, "fresh")
    stale_with_old_entries = await make_session(headers, "stale")
    await join(busy, "Anna")
    await join(stale_with_old_entries, "Boris")
    await old_session(db_session, forgotten, hours_ago=10)
    await old_session(db_session, busy, hours_ago=10)
    await old_session(db_session, stale_with_old_entries, hours_ago=10, entry_hours_ago=9)
    seen = []
    add_listener(seen.append)

    closed = await close_idle_sessions(TestingSessionLocal, hours=6)

    assert sorted(closed) == sorted([forgotten, stale_with_old_entries])
    assert await status_of(client, forgotten) == "closed"
    assert await status_of(client, stale_with_old_entries) == "closed"
    assert await status_of(client, busy) == "open"
    assert await status_of(client, fresh) == "open"
    assert set(closed) <= set(seen)
    assert await close_idle_sessions(TestingSessionLocal, hours=6) == []


@pytest.mark.asyncio
async def test_closed_by_the_reaper_sets_closed_at_and_blocks_new_students(
    client, make_teacher, make_session, db_session
):
    headers = await make_teacher("ops_reaper_join")
    code = await make_session(headers)
    await old_session(db_session, code, hours_ago=7)

    await close_idle_sessions(TestingSessionLocal, hours=6)

    response = await client.post(f"{API}/{code}/join", json={"name": "Late", "token": "l" * 20})
    assert (response.status_code, response.json()["code"]) == (409, "session_closed")
    row = (
        await db_session.execute(
            text("SELECT closed_at FROM queue_sessions WHERE code = :c"), {"c": code}
        )
    ).scalar_one()
    assert row is not None


@pytest.mark.asyncio
async def test_reaper_loop_runs_periodically_and_survives_errors(
    client, make_teacher, make_session, db_session, monkeypatch
):
    headers = await make_teacher("ops_loop")
    code = await make_session(headers)
    await old_session(db_session, code, hours_ago=8)
    from services import maintenance

    real = maintenance.close_idle_sessions
    calls = {"n": 0}

    async def flaky(factory, hours):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return await real(factory, hours)

    monkeypatch.setattr(maintenance, "close_idle_sessions", flaky)
    task = asyncio.create_task(reaper_loop(TestingSessionLocal, 6, 0.05))
    try:
        for _ in range(60):
            if await status_of(client, code) == "closed":
                break
            await asyncio.sleep(0.05)
    finally:
        task.cancel()

    assert await status_of(client, code) == "closed"
    assert calls["n"] >= 2


@pytest.mark.asyncio
async def test_shutdown_closes_open_streams():
    subscriber = Subscriber()
    async with lifespan(app):
        hub._states["SHUT00"] = _SessionState(subscribers={subscriber})

    assert subscriber.closed is True
    assert subscriber.wake.is_set()
    hub._states.clear()
