from collections.abc import AsyncIterator

import httpx
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.db import get_session
from app.main import create_app
from app.models.base import Base

test_engine = create_async_engine(settings.test_database_url)
TestSessionFactory = async_sessionmaker(test_engine, expire_on_commit=False)


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_schema() -> AsyncIterator[None]:
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield
    await test_engine.dispose()


async def _truncate_all() -> None:
    async with TestSessionFactory() as s:
        for table in reversed(Base.metadata.sorted_tables):
            await s.execute(table.delete())
        await s.commit()


@pytest_asyncio.fixture
async def session() -> AsyncIterator[AsyncSession]:
    """A direct session, for tests that exercise the models rather than the API."""
    async with TestSessionFactory() as s:
        yield s
    await _truncate_all()


@pytest_asyncio.fixture
async def anon_client() -> AsyncIterator[httpx.AsyncClient]:
    """An API client with no credentials, for the auth tests themselves.

    Every request gets its OWN session. This matters: `asyncio.gather` over one
    shared AsyncSession raises "another operation is in progress" — asyncpg
    connections are not concurrency-safe. The concurrency tests are the
    centrepiece of this project, so requests must not share a session.
    """
    app = create_app()

    async def override_get_session() -> AsyncIterator[AsyncSession]:
        async with TestSessionFactory() as s:
            yield s

    app.dependency_overrides[get_session] = override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await _truncate_all()


@pytest_asyncio.fixture
async def client(anon_client: httpx.AsyncClient) -> httpx.AsyncClient:
    """The default client: signed in.

    The todo routes are gated, and the tests that use this fixture are about
    todo behaviour rather than authentication — so the token is attached here
    once instead of in every test. Auth itself is exercised through
    `anon_client` in test_auth.py.
    """
    registered = await anon_client.post(
        "/api/auth/register",
        json={"username": "fixture-user", "password": "fixture-password"},
    )
    anon_client.headers["Authorization"] = f"Bearer {registered.json()['access_token']}"
    return anon_client
