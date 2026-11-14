import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings
from core.ratelimit import hourly_limiter, limiter
from core.readiness import expected_revision
from core.security import pwd_context
from db.base import Base
from db.database import get_db
from main import app
from services import presence
from services.ai_drafts import runtime as ai_runtime
from services.auth import login_guard
from services.live import gateway

APP_DATABASE_URL = make_url(settings.DATABASE_URL)
TEST_DATABASE_URL = APP_DATABASE_URL.set(database="queue_test")

assert TEST_DATABASE_URL.database != APP_DATABASE_URL.database

pwd_context.update(bcrypt__rounds=4)

engine = create_async_engine(TEST_DATABASE_URL, echo=False, pool_size=20, max_overflow=20)
TestingSessionLocal = async_sessionmaker(
    bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)


async def override_get_db():
    async with TestingSessionLocal() as db:
        try:
            yield db
        except Exception:
            await db.rollback()
            raise


app.dependency_overrides[get_db] = override_get_db
gateway.session_factory = TestingSessionLocal
ai_runtime.session_factory = TestingSessionLocal


async def create_test_database_if_missing():
    admin_engine = create_async_engine(
        TEST_DATABASE_URL.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    async with admin_engine.connect() as conn:
        exists = await conn.scalar(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": TEST_DATABASE_URL.database},
        )
        if not exists:
            await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_URL.database}"'))
    await admin_engine.dispose()


@pytest_asyncio.fixture(scope="session", autouse=True)
async def prepare_database():
    await create_test_database_if_missing()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        await conn.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await conn.execute(
            text("INSERT INTO alembic_version VALUES (:rev)"), {"rev": expected_revision()}
        )
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    limiter.reset()
    hourly_limiter.reset()
    presence.guard.reset()
    login_guard.reset()


@pytest_asyncio.fixture
async def db_session():
    async with TestingSessionLocal() as session:
        yield session
        await session.rollback()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client


@pytest.fixture
def make_teacher(client):
    async def _make(username: str, password: str = "password123") -> dict:
        response = await client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": password,
                "invite_code": settings.TEACHER_INVITE_CODE,
            },
        )
        assert response.status_code == 201, response.text
        response = await client.post(
            "/api/v1/auth/login", json={"username": username, "password": password}
        )
        assert response.status_code == 200
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _make


@pytest.fixture
def make_session(client):
    async def _make(headers: dict, title: str = "Lab 1") -> str:
        response = await client.post("/api/v1/sessions", headers=headers, json={"title": title})
        assert response.status_code == 201, response.text
        return response.json()["code"]

    return _make


@pytest.fixture
def join(client):
    async def _join(code: str, name: str, token: str | None = None) -> dict:
        token = token or name.ljust(16, "x")
        response = await client.post(
            f"/api/v1/sessions/{code}/join", json={"name": name, "token": token}
        )
        assert response.status_code == 200, response.text
        return {**response.json(), "token": token}

    return _join
