#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Script to add VK tokens to database
"""
import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from database.connection import AsyncSessionLocal
from database.models import VKToken

def _collect_prefixed_env(prefix: str) -> dict[str, str]:
    """
    Collect env vars like VK_TOKEN_VALSTAN=... -> {"VALSTAN": "..."}.
    """
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        if not v or len(v.strip()) < 10:
            continue
        name = k[len(prefix) :].strip("_")
        if not name:
            continue
        out[name.upper()] = v.strip()
    return out


async def main():
    print("🔑 Adding VK tokens to database...")

    tokens = _collect_prefixed_env("VK_TOKEN_")
    if not tokens:
        print("❌ No VK_TOKEN_* env vars found.")
        print("💡 See: config/setka.env.example")
        return 1
    
    async with AsyncSessionLocal() as session:
        added_count = 0
        
        for name, token in tokens.items():
            
            # Check if exists
            result = await session.execute(
                select(VKToken).where(VKToken.name == name)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                print(f"  ⏭️  Token {name} already exists")
                continue
            
            vk_token = VKToken(
                name=name,
                token=token,
                is_active=True
            )
            
            session.add(vk_token)
            added_count += 1
            print(f"  ✅ Added: {name}")
        
        await session.commit()
        print(f"\n✅ Added {added_count} VK tokens")
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

