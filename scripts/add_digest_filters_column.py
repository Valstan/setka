#!/usr/bin/env python3
"""Добавить колонку region_configs.bulletin_filters (идемпотентно)."""
import asyncio
import os
import sys

from sqlalchemy import text


async def main() -> None:
    from sqlalchemy.ext.asyncio import create_async_engine

    raw = os.environ.get("DATABASE_URL", "")
    if not raw:
        print("DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)
    eng = create_async_engine(raw)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "ALTER TABLE region_configs "
                "ADD COLUMN IF NOT EXISTS bulletin_filters JSONB DEFAULT NULL"
            )
        )
    await eng.dispose()
    print("OK: bulletin_filters column")


if __name__ == "__main__":
    asyncio.run(main())
