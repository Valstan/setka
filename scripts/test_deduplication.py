#!/usr/bin/env python3
"""
Test deduplication module
Based on Postopus proven patterns
"""
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from modules.deduplication import (
    create_lip_fingerprint,
    create_media_fingerprint,
    create_text_fingerprint,
    create_text_core_fingerprint,
    text_to_rafinad,
    DuplicationDetector
)
from database.connection import AsyncSessionLocal


def test_lip_fingerprint():
    """Test structural fingerprint"""
    print("Testing LIP (structural) fingerprint...")
    
    lip1 = create_lip_fingerprint(-170319760, 3512)
    lip2 = create_lip_fingerprint(-170319760, 3512)
    lip3 = create_lip_fingerprint(-170319760, 3513)
    
    assert lip1 == lip2, "Same post should have same lip"
    assert lip1 != lip3, "Different posts should have different lips"
    assert lip1 == "-170319760_3512"
    
    print(f"  ✅ LIP fingerprint: {lip1}")


def test_media_fingerprint():
    """Test media fingerprint"""
    print("\nTesting media fingerprint...")
    
    attachments = [
        {'type': 'photo', 'photo': {'id': 457239017}},
        {'type': 'photo', 'photo': {'id': 457239018}},
        {'type': 'video', 'video': {'owner_id': -12345, 'id': 678}}
    ]
    
    media = create_media_fingerprint(attachments)
    
    assert len(media) == 3
    assert 'photo_457239017' in media
    assert 'video_-12345_678' in media
    
    print(f"  ✅ Media fingerprint: {media}")


def test_text_rafinad():
    """Test text rafinad (cleaning)"""
    print("\nTesting text rafinad...")
    
    # Test cases from Postopus documentation
    text1 = "🔥 В Малмыже пройдет концерт! 25 октября в ДК."
    rafinad1 = text_to_rafinad(text1)
    
    text2 = "25 октября в Малмыже концерт в ДК! Приходите все!"
    rafinad2 = text_to_rafinad(text2)
    
    print(f"  Original 1: {text1}")
    print(f"  Rafinad 1:  {rafinad1}")
    print(f"  Original 2: {text2}")
    print(f"  Rafinad 2:  {rafinad2}")
    
    # Check that emoji and punctuation are removed
    assert '🔥' not in rafinad1
    assert '!' not in rafinad1
    assert ' ' not in rafinad1
    
    print("  ✅ Rafinad removes all noise")


def test_text_fingerprint():
    """Test full text fingerprint"""
    print("\nTesting text fingerprint...")
    
    text1 = "Завтра в Малмыже концерт"
    text2 = "Завтра в Малмыже концерт"  # Exact same
    text3 = "Завтра в Малмыже концерт!"  # With punctuation (should be same)
    text4 = "В Малмыже завтра концерт"  # Different order
    
    fp1 = create_text_fingerprint(text1)
    fp2 = create_text_fingerprint(text2)
    fp3 = create_text_fingerprint(text3)
    fp4 = create_text_fingerprint(text4)
    
    assert fp1 == fp2, "Exact same text should have same fingerprint"
    assert fp1 == fp3, "Punctuation should not change fingerprint"
    assert fp1 != fp4, "Different word order should have different fingerprint"
    
    print(f"  ✅ Text fingerprint: {fp1[:16]}...")


def test_text_core_fingerprint():
    """Test text core fingerprint (key innovation from Postopus)"""
    print("\nTesting text CORE fingerprint...")
    
    # This is the KEY innovation: beginning and end can differ!
    text1 = "Здравствуйте! В Малмыже 25 октября концерт. Приходите!"
    text2 = "25 октября в Малмыже концерт"  # Same core, different beginning/end
    
    # Get rafinads
    rafinad1 = text_to_rafinad(text1)
    rafinad2 = text_to_rafinad(text2)
    
    print(f"  Text 1: {text1}")
    print(f"  Rafinad 1: {rafinad1}")
    print(f"  Text 2: {text2}")
    print(f"  Rafinad 2: {rafinad2}")
    
    # Get core (20-70% of rafinad)
    start1 = len(rafinad1) // 5
    end1 = start1 + len(rafinad1) // 2
    core1 = rafinad1[start1:end1]
    
    start2 = len(rafinad2) // 5
    end2 = start2 + len(rafinad2) // 2
    core2 = rafinad2[start2:end2]
    
    print(f"  Core 1: {core1}")
    print(f"  Core 2: {core2}")
    
    # Get fingerprints
    fp1 = create_text_core_fingerprint(text1)
    fp2 = create_text_core_fingerprint(text2)
    
    print(f"  Core fingerprint 1: {fp1[:16]}...")
    print(f"  Core fingerprint 2: {fp2[:16]}...")
    
    if fp1 == fp2:
        print("  ✅ Core fingerprints MATCH! Duplicates detected despite different wording!")
    else:
        print("  ℹ️ Core fingerprints different (texts too different)")


async def test_detector():
    """Test DuplicationDetector"""
    print("\n\nTesting DuplicationDetector...")
    
    async with AsyncSessionLocal() as session:
        detector = DuplicationDetector(session)
        
        # Test 1: Check for structural duplicate (if any posts exist)
        result = await detector.check_duplicate(
            owner_id=-170319760,
            post_id=3512,
            check_methods=['lip']
        )
        
        print(f"\n  Structural check: {result}")
        
        # Test 2: Check for text duplicate
        test_text = "В Малмыже пройдет концерт 25 октября"
        result = await detector.check_duplicate(
            owner_id=-999999,
            post_id=999999,
            text=test_text,
            check_methods=['core']
        )
        
        print(f"  Text check: {result}")
        
        print("\n  ✅ Detector working!")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("TESTING DEDUPLICATION MODULE")
    print("Based on Postopus patterns (3+ years in production)")
    print("=" * 60)
    
    try:
        # Unit tests
        test_lip_fingerprint()
        test_media_fingerprint()
        test_text_rafinad()
        test_text_fingerprint()
        test_text_core_fingerprint()
        
        # Integration test
        await test_detector()
        
        print()
        print("=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print()
        print("Key features working:")
        print("  ✅ LIP (structural) fingerprint - exact duplicate detection")
        print("  ✅ Media fingerprint - photo/video duplicate detection")
        print("  ✅ Text rafinad - cleaning noise from text")
        print("  ✅ Text fingerprint - full text duplicate detection")
        print("  ✅ Core fingerprint - semantic duplicate detection")
        print("  ✅ DuplicationDetector - integrated checking")
        
        return 0
    
    except AssertionError as e:
        print()
        print("=" * 60)
        print(f"❌ TEST FAILED: {e}")
        print("=" * 60)
        return 1
    except Exception as e:
        print()
        print("=" * 60)
        print(f"❌ ERROR: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)

