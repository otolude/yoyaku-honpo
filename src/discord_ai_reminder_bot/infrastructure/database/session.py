"""Creation of SQLAlchemy engines and session factories."""

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database_engine(database_url: SecretStr) -> AsyncEngine:
    """Create an async engine without opening a database connection.

    The URL is unwrapped only at this infrastructure boundary. SQL statement
    parameters are hidden and SQL echo is disabled so credentials and values are
    not emitted by SQLAlchemy logging.
    """
    return create_async_engine(
        database_url.get_secret_value(),
        echo=False,
        hide_parameters=True,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Create a factory that gives each operation its own AsyncSession."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        expire_on_commit=False,
    )
