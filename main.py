import asyncio
import contextlib
import hmac
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError
from starlette.exceptions import HTTPException as StarletteHTTPException

from core.config import settings
from core.cors import configure_cors
from core.dependencies import DbSession
from core.exceptions import AppError
from core.http import RequestContextMiddleware, error_body
from core.limits import ConcurrencyLimitMiddleware
from core.limits import stats as limit_stats
from core.logging import setup_logging
from core.metrics import metrics
from core.ratelimit import limit_ip, limiter
from core.readiness import schema_is_current
from db.database import AsyncSessionLocal, engine
from routers.ai import router as ai_router
from routers.analytics import router as analytics_router
from routers.auth import router as auth_router
from routers.budget import router as budget_router
from routers.history import router as history_router
from routers.live import router as live_router
from routers.practice import router as practice_router
from routers.protocol import router as protocol_router
from routers.qr import router as qr_router
from routers.sessions import router as sessions_router
from routers.spaces import router as spaces_router
from routers.templates import router as templates_router
from services.ai_drafts import runtime as ai_runtime
from services.live import hub
from services.maintenance import reaper_loop
from services.practice import runtime as practice_runtime
from services.retention import retention_loop

setup_logging(settings.LOG_LEVEL, settings.LOG_FORMAT)
logger = logging.getLogger(__name__)

API_V1_PREFIX = "/api/v1"
STATIC_DIR = Path(__file__).resolve().parent / "static"
OVERLOAD_RETRY_AFTER_SECONDS = 2

metrics.gauge("http_requests_in_flight", lambda: limit_stats.active)
metrics.gauge("sse_connections", hub.count)
metrics.gauge("db_pool_checked_out", lambda: engine.pool.checkedout())  # type: ignore[attr-defined]
metrics.counter_source("http_rejected_overload_total", lambda: limit_stats.rejected)
metrics.counter_source("http_rejected_rate_limit_total", lambda: limiter.rejected)
metrics.counter_source("sse_refreshes_total", lambda: hub.refreshes)
metrics.counter_source("sse_rejected_total", lambda: hub.rejected)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    reaper = None
    retention = None
    try:
        await ai_runtime.sweep_interrupted()
    except Exception:
        logger.warning("Could not mark interrupted draft jobs", exc_info=True)
    if settings.SESSION_AUTO_CLOSE_HOURS > 0:
        reaper = asyncio.create_task(
            reaper_loop(
                AsyncSessionLocal,
                settings.SESSION_AUTO_CLOSE_HOURS,
                settings.SESSION_REAPER_INTERVAL_SECONDS,
            )
        )
    if settings.DATA_RETENTION_DAYS > 0:
        retention = asyncio.create_task(
            retention_loop(
                AsyncSessionLocal,
                settings.DATA_RETENTION_DAYS,
                settings.RETENTION_INTERVAL_SECONDS,
            )
        )
    try:
        yield
    finally:
        if retention is not None:
            retention.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await retention
        if reaper is not None:
            reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await reaper
        await ai_runtime.shutdown()
        await practice_runtime.shutdown()
        await hub.close()
        await engine.dispose()


app = FastAPI(
    lifespan=lifespan,
    dependencies=[Depends(limit_ip)],
    title="Queue API",
    description="Live queue for in-class assignment checks: students join by session code, "
    "see their position and estimated wait, the teacher calls the next student.",
    version="0.1.0",
)

app.include_router(auth_router, prefix=API_V1_PREFIX)
app.include_router(sessions_router, prefix=API_V1_PREFIX)
app.include_router(budget_router, prefix=API_V1_PREFIX)
app.include_router(qr_router, prefix=API_V1_PREFIX)
app.include_router(live_router, prefix=API_V1_PREFIX)
app.include_router(protocol_router, prefix=API_V1_PREFIX)
app.include_router(ai_router, prefix=API_V1_PREFIX)
app.include_router(practice_router, prefix=API_V1_PREFIX)
app.include_router(analytics_router, prefix=API_V1_PREFIX)
app.include_router(history_router, prefix=API_V1_PREFIX)
app.include_router(templates_router, prefix=API_V1_PREFIX)
app.include_router(spaces_router, prefix=API_V1_PREFIX)


def _error(status: int, detail: str, code: str, headers: dict | None = None, **extra):
    return JSONResponse(error_body(detail, code, **extra), status_code=status, headers=headers)


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return _error(exc.status_code, exc.detail, exc.code, exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    errors = [
        {
            "loc": ".".join(str(part) for part in e["loc"]),
            "msg": e["msg"],
            "type": e["type"],
            "ctx": {k: v for k, v in (e.get("ctx") or {}).items() if isinstance(v, int)},
        }
        for e in exc.errors()
    ]
    return _error(422, "Validation error", "validation_error", errors=errors)


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(request: Request, exc: StarletteHTTPException):
    codes = {404: "not_found", 405: "method_not_allowed"}
    return _error(exc.status_code, str(exc.detail), codes.get(exc.status_code, "http_error"))


@app.exception_handler(PoolTimeoutError)
async def pool_timeout_handler(request: Request, exc: PoolTimeoutError):
    logger.warning("DB pool exhausted on %s %s: %s", request.method, request.url.path, exc)
    return _error(
        503,
        "Service is overloaded, retry later",
        "overloaded",
        {"Retry-After": str(OVERLOAD_RETRY_AFTER_SECONDS)},
    )


@app.exception_handler(OperationalError)
@app.exception_handler(InterfaceError)
async def database_unavailable_handler(request: Request, exc: Exception):
    logger.error("Database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return _error(
        503,
        "Database is unavailable, retry later",
        "database_unavailable",
        {"Retry-After": str(OVERLOAD_RETRY_AFTER_SECONDS)},
    )


@app.get("/health", tags=["Ops"])
async def health():
    return {"status": "ok"}


@app.get("/ready", tags=["Ops"])
async def ready(db: DbSession):
    try:
        current = await schema_is_current(db)
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        return _error(
            503,
            "Database is not ready",
            "not_ready",
            {"Retry-After": str(OVERLOAD_RETRY_AFTER_SECONDS)},
        )
    if not current:
        return _error(503, "Database schema is not at the expected revision", "schema_outdated")
    return {"status": "ready"}


@app.get("/metrics", tags=["Ops"], include_in_schema=False)
async def metrics_endpoint(authorization: str | None = Header(default=None)):
    if settings.METRICS_TOKEN:
        expected = f"Bearer {settings.METRICS_TOKEN}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            return _error(401, "Not authenticated", "not_authenticated")
    return PlainTextResponse(metrics.render(), media_type="text/plain; version=0.0.4")


@app.get("/", include_in_schema=False)
async def student_page():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/teacher", include_in_schema=False)
async def teacher_page():
    return FileResponse(STATIC_DIR / "teacher.html")


@app.get("/protocol", include_in_schema=False)
async def protocol_page():
    return FileResponse(STATIC_DIR / "protocol.html")


@app.get("/students", include_in_schema=False)
async def students_page():
    return FileResponse(STATIC_DIR / "students.html")


@app.get("/spaces", include_in_schema=False)
async def spaces_page():
    return FileResponse(STATIC_DIR / "spaces.html")


@app.get("/practice", include_in_schema=False)
async def practice_page():
    return FileResponse(STATIC_DIR / "practice.html")


app.add_middleware(ConcurrencyLimitMiddleware)
app.add_middleware(RequestContextMiddleware, access_log=settings.ACCESS_LOG)
configure_cors(app, settings.CORS_ORIGINS)
