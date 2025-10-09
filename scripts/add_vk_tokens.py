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

# VK Tokens (from config file)
TOKENS = {
    # REPLACE WITH YOUR ACTUAL TOKENS FROM config/config_secure.py
    "VK_TOKEN_VALSTAN": "vk1.a.YOUR_TOKEN_HERE",
    "VK_TOKEN_OLGA": "vk1.a.YOUR_TOKEN_HERE",
    "VK_TOKEN_VITA": "vk1.a.YOUR_TOKEN_HERE"
}


async def main():
    print("🔑 Adding VK tokens to database...")
    
    async with AsyncSessionLocal() as session:
        added_count = 0
        
        for name, token in TOKENS.items():
            if not token:
                continue
            
            # Check if exists
            result = await session.execute(
                select(VKToken).where(VKToken.name == name)
            )
            existing = result.scalar_one_or_none()
            
            if existing:
                print(f"  ⏭️  Token {name} already exists")
                continue
            
            # Determine usage type
            if 'VALSTAN' in name:
                usage_type = 'post'
            else:
                usage_type = 'read'
            
            vk_token = VKToken(
                name=name,
                token=token,
                usage_type=usage_type,
                is_active=True
            )
            
            session.add(vk_token)
            added_count += 1
            print(f"  ✅ Added: {name}")
        
        await session.commit()
        print(f"\n✅ Added {added_count} VK tokens")


if __name__ == "__main__":
    asyncio.run(main())

