import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.database import get_db
from main import app
from tests.conftest import TEST_DATABASE_URL


@pytest_asyncio.fixture
async def exhausted_pool():
    small = create_async_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0, pool_timeout=0.3)
    factory = async_sessionmaker(bind=small, class_=AsyncSession, expire_on_commit=False)
    previous = app.dependency_overrides[get_db]

    async def small_pool_db():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = small_pool_db
    holder = factory()
    await holder.execute(text("SELECT 1"))
    try:
        yield holder
    finally:
        await holder.close()
        app.dependency_overrides[get_db] = previous
        await small.dispose()


@pytest.mark.asyncio
async def test_exhausted_connection_pool_returns_503_with_retry_after(client, exhausted_pool):
    response = await client.get("/api/v1/sessions/ABCDEF/me", headers={"X-Entry-Token": "x" * 16})

    assert response.status_code == 503
    assert int(response.headers["Retry-After"]) >= 1
    body = response.json()
    assert (body["detail"], body["code"]) == ("Service is overloaded, retry later", "overloaded")
    assert body["request_id"] == response.headers["X-Request-ID"]


@pytest.mark.asyncio
async def test_service_recovers_when_connection_is_released(client, exhausted_pool):
    assert (await client.get("/ready")).status_code == 503

    await exhausted_pool.close()

    assert (await client.get("/ready")).status_code == 200
