from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import require_database_url, settings

# Before the engine, not in `create_app()`: this module is imported long before
# the app is built, so an empty URL would surface as a driver parse error during
# import rather than as an explanation.
require_database_url()

engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionFactory() as session:
        yield session


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """The factory itself, for work that needs a session per unit rather than
    one per request — batch items, which must commit or roll back individually.

    Exposed as a dependency purely so it can be overridden. Reaching for the
    module-level `SessionFactory` directly would work in production and quietly
    point the test suite at the developer's real database.
    """
    return SessionFactory
