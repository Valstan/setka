"""
Unit tests for the advertisement filter.

Tests do NOT require database or external services.
"""
import pytest
from modules.filters.ads_filter import AdvertisementFilter


@pytest.fixture
def ad_filter():
    return AdvertisementFilter(name="advertisement_filter")


class TestAdvertisementFilter:
    """Tests for AdvertisementFilter."""

    @pytest.mark.asyncio
    async def test_reklama_theme_skipped(self, ad_filter):
        """Posts with theme='reklama' should skip ad detection."""
        post = {"text": "Купить недорого!", "marked_as_ads": False}
        context = {"theme": "reklama"}
        result = await ad_filter.apply(post, context)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_vk_marked_as_ads_rejected(self, ad_filter):
        """Posts marked_as_ads=True should be rejected."""
        post = {"text": "Some post", "marked_as_ads": True}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False
        assert "VK API marked as ads" in result.reason

    @pytest.mark.asyncio
    async def test_legal_marker_rejected(self, ad_filter):
        """Posts with #реклама should be rejected."""
        post = {"text": "Отличный товар #реклама", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False
        assert "Legal ad marker" in result.reason

    @pytest.mark.asyncio
    async def test_hash_ad_rejected(self, ad_filter):
        """Posts with #ad should be rejected."""
        post = {"text": "Best product #ad", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_erid_rejected(self, ad_filter):
        """Posts with erid: should be rejected."""
        post = {"text": "Спонсор поста erid:abc123", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_commercial_price_rejected(self, ad_filter):
        """Posts with price patterns should be rejected."""
        post = {"text": "Продаю гараж цена5000руб скидка недорого", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_commercial_rub_rejected(self, ad_filter):
        """Posts with 'руб' pattern should be rejected."""
        post = {"text": "Всего 100руб за штуку купить заказать бесплатно", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_news_passes(self, ad_filter):
        """Regular news posts should pass."""
        post = {"text": "Сегодня в городе прошла конференция по технологиям", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_empty_text_passes(self, ad_filter):
        """Empty text should pass (not an ad)."""
        post = {"text": "", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_suspicious_link_rejected(self, ad_filter):
        """Posts with vk.com/ads links should be rejected."""
        post = {"text": "Реклама vk.com/ads/campaign купить недорого 100руб", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False

    @pytest.mark.asyncio
    async def test_sponsored_hashtag_rejected(self, ad_filter):
        """Posts with #sponsored should be rejected."""
        post = {"text": "Обзор нового смартфона #sponsored", "marked_as_ads": False}
        context = {"theme": "novost"}
        result = await ad_filter.apply(post, context)
        assert result.passed is False
