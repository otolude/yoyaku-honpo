"""Read-only database connectivity check."""

from __future__ import annotations

import asyncio

from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from discord_ai_reminder_bot.config import load_database_settings
from discord_ai_reminder_bot.infrastructure.database.session import create_database_engine


async def check_database_connection(engine: AsyncEngine) -> None:
    """Execute a read-only SELECT 1 using an existing engine."""
    async with engine.connect() as connection:
        result = await connection.execute(text("SELECT 1"))
        if result.scalar_one() != 1:
            raise RuntimeError("データベース接続確認で想定外の結果を受け取りました")


async def verify_database_connection(database_url: SecretStr) -> None:
    """Create an engine, check connectivity, and always dispose the engine."""
    engine = create_database_engine(database_url)
    try:
        await check_database_connection(engine)
    finally:
        await engine.dispose()


def main() -> int:
    """Run the connectivity check without starting the Discord Bot."""
    settings = load_database_settings()
    try:
        asyncio.run(verify_database_connection(settings.database_url))
    except Exception:  # noqa: BLE001 -- CLI must not print driver errors containing connection data.
        print("PostgreSQLへの接続確認に失敗しました。設定と起動状態を確認してください。")
        return 1

    print("PostgreSQLへの接続確認に成功しました（SELECT 1）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
