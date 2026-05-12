"""Tests for region words filter in advanced parser."""
import os
import sys
import time
import asyncio
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.vk_monitor.advanced_parser import AdvancedVKParser
from modules.publisher.digest_builder import DigestBuilder


def test_region_words_filter_logic():
    """Test the region words filtering logic directly."""
    parser = AdvancedVKParser(vk_client=None)
    
    # Initialize batch attributes like parse_posts_from_communities does
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    
    # Test post without region keyword - should be rejected
    post_data = {
        "owner_id": -123,
        "community_vk_id": -123,
        "id": 1,
        "date": int(time.time()) - 3600,  # 1 hour ago
        "text": "✍ 👋Хәләл чеби-бройлерлар, натураль кормаларда үстерелгән\nБер чеби ~2,5-3кг\n..."
    }
    region_config = SimpleNamespace(
        filter_group_by_region_words={"123": ["малмыж", "малмыжский"]},
        black_id=[],
        delete_msg_blacklist=[]
    )

    filtered = asyncio.run(parser._filter_post(
        post_data,
        theme="sport",
        region_config=region_config,
        work_table_lip=[],
        work_hash_set=set(),
        recent_text_fingerprints=set(),
    ))

    assert filtered is None
    assert parser.stats["posts_filtered_no_region_words"] == 1


def test_region_words_filter_accepts_and_hides_attribution():
    """Test that posts with region keywords are accepted and attribution is hidden."""
    parser = AdvancedVKParser(vk_client=None)
    
    # Initialize batch attributes
    parser._batch_lips = set()
    parser._batch_text_fps = set()
    parser._batch_core_fps = set()
    parser._batch_media_sigs = set()
    parser._batch_text_simhashes = set()
    
    # Test post with region keyword - should be accepted and attribution hidden
    post_data = {
        "owner_id": -123,
        "community_vk_id": -123,
        "id": 2,
        "date": int(time.time()) - 3600,  # 1 hour ago
        "text": "Малмыж бүләк ителә, зур бәядә сату өчен.",
        "attachments": [{"type": "photo", "photo": {"id": 1}}]  # Add attachment to pass no-attachments filter
    }
    region_config = SimpleNamespace(
        filter_group_by_region_words={"123": ["малмыж", "малмыжский"]},
        black_id=[],
        delete_msg_blacklist=[]
    )

    filtered = asyncio.run(parser._filter_post(
        post_data,
        theme="sport",
        region_config=region_config,
        work_table_lip=[],
        work_hash_set=set(),
        recent_text_fingerprints=set(),
    ))

    assert filtered is not None
    assert filtered.get("hide_attribution") is True

    builder = DigestBuilder(header="⚽ СПОРТ", hashtags=["#спорт"], local_hashtag="#малмыж")
    digest = builder.build_digest([filtered], group_names={"123": "МалмыЖ"})

    assert "Источник" not in digest.text
    assert "@https://vk.com/wall-123_2" not in digest.text
    assert "Малмыж" in digest.text