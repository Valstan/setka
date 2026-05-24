#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Test Groq AI API
"""
import asyncio
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import Groq API key from config
from config.runtime import GROQ_API_KEY  # noqa: E402
from modules.ai_analyzer.groq_client import GroqClient  # noqa: E402


async def test_groq_analysis():
    """Test Groq AI analysis"""
    print("=" * 70)
    print("🧪 Testing Groq AI API")
    print("=" * 70)

    # Test texts
    test_posts = [
        {
            "id": 1,
            "text": (
                "В регионе действует 40 пунктов проката технических средств "
                "реабилитации для инвалидов и пожилых людей"
            ),
        },
        {
            "id": 2,
            "text": (
                "Продаются 6-ти месячные индоутки тяжёлой линии. "
                "Красный бык, уточки 1500, селезни 2500. 89172715085"
            ),
        },
        {
            "id": 3,
            "text": (
                "Администрация района объявляет о проведении общественных слушаний "
                "по вопросу строительства нового детского сада"
            ),
        },
    ]

    # Create client
    client = GroqClient(api_key=GROQ_API_KEY)

    print(f"\n✅ Groq API Key: {GROQ_API_KEY[:20]}...")
    print("✅ Client created\n")

    # Analyze each post
    for post in test_posts:
        print(f"\n{'='*70}")
        print(f"📝 Post {post['id']}: {post['text'][:60]}...")
        print(f"{'='*70}")

        try:
            result = await client.analyze_post(post["text"])

            print("\n📊 AI Analysis:")
            print(f"   Category: {result.get('category', 'N/A')}")
            print(f"   Relevance: {result.get('relevance', 'N/A')}/100")
            print(f"   Is Spam: {result.get('is_spam', 'N/A')}")
            print(f"   Reason: {result.get('reason', 'N/A')}")

            if "raw_response" in result:
                print(f"\n   Raw AI response: {result['raw_response'][:200]}...")

        except Exception as e:
            print(f"   ❌ Error: {e}")

    print("\n" + "=" * 70)
    print("✅ Groq AI test completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(test_groq_analysis())
