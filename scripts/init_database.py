#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to initialize database tables
"""
import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.connection import engine, init_db


async def main():
    print("🔧 Initializing SETKA database...")

    try:
        await init_db()
        print("✅ Database tables created successfully!")

        # Test connection
        async with engine.connect():
            print("✅ Database connection test successful!")

    except Exception as e:
        print(f"❌ Error initializing database: {e}")
        import traceback

        traceback.print_exc()
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
