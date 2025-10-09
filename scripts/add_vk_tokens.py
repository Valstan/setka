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
    "VK_TOKEN_VALSTAN": "vk1.a.gczp291vx4VkA5hZRP9lwpJVtSCTx-c79D7zGM3pmAub0YszQXL-DIK5mY0xry-XEKWbiTzSiADxNAEQRHfUzCH1XsEh-BoCStWNNOp_TBY_GOOzhkQtPfDxbbntkuVHSBy3Jeunedmp_om-28OvYgZy51IPi2jfyh5yic7-oTutbe8NMVsNdAyhfhpcAUPy8J2wiTOWrR0L0QE8KMudrQ",
    "VK_TOKEN_OLGA": "vk1.a.YB3vu9mP072pkadsec7VVBDaIjke_VByDUks3QnLaWsbbu28M5SkhDvik6I_97VsdQs9-gSvPQ1U6FBr4a-a866Gu7xcXcPRLWU2UKmThfqAwJXoSS4cfDgap-frRec_Yqg3jZLyl29a-xNcQSsZN74ydv0W7swkFNrr8UHIlkoNQZjiDNJvqB2SxuIuBu3uGU2AiGqdasw9SBN9kDFXAA",
    "VK_TOKEN_VITA": "vk1.a.h8ZMyCgenUYgB6Ci8MKpi6AFVS9lXy4ndWrVPJu0BT4uncFFM3vmi8qJeUGpW-7X0DBhBWfQHs9qrIzo5CS2LkbpOnNo563B4XtY5DT-JPLYguCRQkmrEdcx7YQQQgzIALlB8bbQeyub32BJtZQvEs12xdcYXBHD85SUxJ2l6cuYjVj0gL5pqMR17xmlbxav3tx83eikViL1JH80Twipdw"
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

