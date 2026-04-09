"""
Unit tests for the filter pipeline and base filter classes.

Tests do NOT require database or external services.
"""
import pytest
import asyncio
from modules.filters.base import BaseFilter, FilterResult
from modules.filters.pipeline import FilterPipeline, PipelineResult


# ---------------------------------------------------------------------------
# FilterResult tests
# ---------------------------------------------------------------------------

class TestFilterResult:
    """Tests for FilterResult dataclass."""

    def test_accept_result(self):
        """Should create a passing result."""
        result = FilterResult(passed=True)
        assert result.passed is True
        assert result.reason is None
        assert result.score_modifier == 0
        assert result.metadata == {}

    def test_reject_result(self):
        """Should create a failing result with reason."""
        result = FilterResult(passed=False, reason="blacklisted")
        assert result.passed is False
        assert result.reason == "blacklisted"

    def test_score_modifier(self):
        """Should support score modifiers."""
        result = FilterResult(passed=True, score_modifier=10)
        assert result.score_modifier == 10

    def test_metadata_defaults_to_empty_dict(self):
        """Should default metadata to empty dict."""
        result = FilterResult(passed=True)
        assert result.metadata == {}
        # Ensure mutable default is not shared
        result2 = FilterResult(passed=True)
        result.metadata["foo"] = "bar"
        assert "foo" not in result2.metadata


# ---------------------------------------------------------------------------
# Concrete filter implementation for testing
# ---------------------------------------------------------------------------

class DummyFilter(BaseFilter):
    """A simple filter for testing."""

    async def apply(self, post, context):
        if context.get("reject_all"):
            return FilterResult(passed=False, reason="reject_all context")
        return FilterResult(passed=True)


class AlwaysRejectFilter(BaseFilter):
    """A filter that always rejects."""

    async def apply(self, post, context):
        return FilterResult(passed=False, reason="always rejects")


# ---------------------------------------------------------------------------
# BaseFilter tests
# ---------------------------------------------------------------------------

class TestBaseFilter:
    """Tests for BaseFilter abstract class."""

    @pytest.mark.asyncio
    async def test_dummy_filter_accepts(self):
        """Dummy filter should accept all posts."""
        f = DummyFilter("test")
        result = await f.apply({}, {})
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_always_reject_filter(self):
        """AlwaysRejectFilter should reject all posts."""
        f = AlwaysRejectFilter("reject")
        result = await f.apply({}, {})
        assert result.passed is False

    def test_filter_stats_initialization(self):
        """Filter should initialize stats dict."""
        f = DummyFilter("test", priority=10)
        assert f.stats == {"total_checked": 0, "passed": 0, "filtered": 0}
        assert f.priority == 10

    @pytest.mark.asyncio
    async def test_update_stats_on_pass(self):
        """Stats should update when post passes."""
        f = DummyFilter("test")
        result = await f.apply({}, {})
        f.update_stats(result)
        assert f.stats["total_checked"] == 1
        assert f.stats["passed"] == 1
        assert f.stats["filtered"] == 0

    @pytest.mark.asyncio
    async def test_update_stats_on_filter(self):
        """Stats should update when post is filtered."""
        f = AlwaysRejectFilter("reject")
        result = await f.apply({}, {})
        f.update_stats(result)
        assert f.stats["total_checked"] == 1
        assert f.stats["passed"] == 0
        assert f.stats["filtered"] == 1

    def test_get_stats(self):
        """Should return stats dict with filter rate."""
        f = DummyFilter("test")
        f.stats = {"total_checked": 10, "passed": 7, "filtered": 3}
        stats = f.get_stats()
        assert stats["name"] == "test"
        assert stats["total_checked"] == 10
        assert stats["filter_rate"] == "30.0%"

    def test_reset_stats(self):
        """Should reset stats to initial state."""
        f = DummyFilter("test")
        f.stats = {"total_checked": 10, "passed": 7, "filtered": 3}
        f.reset_stats()
        assert f.stats == {"total_checked": 0, "passed": 0, "filtered": 0}


# ---------------------------------------------------------------------------
# FilterPipeline tests
# ---------------------------------------------------------------------------

class TestFilterPipeline:
    """Tests for FilterPipeline."""

    @pytest.mark.asyncio
    async def test_all_pass(self):
        """Posts should pass when all filters accept."""
        pipeline = FilterPipeline([DummyFilter("f1"), DummyFilter("f2")])
        posts = [{"id": 1}, {"id": 2}]
        passed, result = await pipeline.process(posts, {})
        assert len(passed) == 2
        assert result.passed_count == 2
        assert result.filtered_count == 0

    @pytest.mark.asyncio
    async def test_all_filtered(self):
        """All posts should be filtered out."""
        pipeline = FilterPipeline([AlwaysRejectFilter("r1")])
        posts = [{"id": 1}, {"id": 2}]
        passed, result = await pipeline.process(posts, {})
        assert len(passed) == 0
        assert result.passed_count == 0
        assert result.filtered_count == 2

    @pytest.mark.asyncio
    async def test_partial_filter(self):
        """Some posts should pass and some should be filtered."""
        class HalfFilter(BaseFilter):
            async def apply(self, post, context):
                if post.get("id") % 2 == 0:
                    return FilterResult(passed=True)
                return FilterResult(passed=False, reason="odd id")

        pipeline = FilterPipeline([HalfFilter("half")])
        posts = [{"id": 1}, {"id": 2}, {"id": 3}, {"id": 4}]
        passed, result = await pipeline.process(posts, {})
        assert len(passed) == 2
        assert result.passed_count == 2
        assert result.filtered_count == 2

    @pytest.mark.asyncio
    async def test_empty_posts(self):
        """Should handle empty posts list."""
        pipeline = FilterPipeline([DummyFilter("f1")])
        passed, result = await pipeline.process([], {})
        assert len(passed) == 0
        assert result.original_count == 0

    @pytest.mark.asyncio
    async def test_filter_priority_sorting(self):
        """Filters should be sorted by priority."""
        f1 = DummyFilter("low", priority=100)
        f2 = DummyFilter("high", priority=1)
        pipeline = FilterPipeline([f1, f2])
        assert pipeline.filters[0].name == "high"
        assert pipeline.filters[1].name == "low"

    @pytest.mark.asyncio
    async def test_context_reject_all(self):
        """Context flag should cause all posts to be rejected."""
        pipeline = FilterPipeline([DummyFilter("f1")])
        posts = [{"id": 1}]
        passed, result = await pipeline.process(posts, {"reject_all": True})
        assert len(passed) == 0

    @pytest.mark.asyncio
    async def test_pipeline_result_stats(self):
        """Pipeline result should include per-filter stats."""
        pipeline = FilterPipeline([DummyFilter("f1")])
        posts = [{"id": 1}]
        _, result = await pipeline.process(posts, {})
        assert len(result.filter_stats) == 1
        assert result.filter_stats[0]["name"] == "f1"
        assert result.processing_time >= 0

    def test_get_statistics(self):
        """Should return pipeline statistics."""
        pipeline = FilterPipeline([DummyFilter("f1")])
        stats = pipeline.get_statistics()
        assert stats["total_filters"] == 1
        assert len(stats["filters"]) == 1
