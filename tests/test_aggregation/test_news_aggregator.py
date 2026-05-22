from dataclasses import dataclass

import pytest

from modules.aggregation.aggregator import NewsAggregator


@dataclass
class FakePost:
    text: str = ""
    views: int = 0
    likes: int = 0
    reposts: int = 0
    vk_owner_id: int = 1
    vk_post_id: int = 1
    ai_category: str = "novost"


@pytest.mark.asyncio
async def test_aggregate_returns_none_for_posts_without_text():
    aggregator = NewsAggregator()
    posts = [FakePost(text="   "), FakePost(text="")]

    digest = await aggregator.aggregate(posts=posts, title="📰 Тест", hashtags=["#test"])

    assert digest is None


@pytest.mark.asyncio
async def test_aggregate_keeps_posts_with_text():
    aggregator = NewsAggregator()
    posts = [FakePost(text=""), FakePost(text="Короткий текст поста", views=150)]

    digest = await aggregator.aggregate(posts=posts, title="📰 Тест", hashtags=["#test"])

    assert digest is not None
    assert "Короткий текст поста" in digest.aggregated_text
    assert "📰 Тест" in digest.aggregated_text
    assert "#test" in digest.aggregated_text
