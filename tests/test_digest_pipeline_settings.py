"""Слияние digest_filters с дефолтами."""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.digest_pipeline_settings import get_effective_pipeline_settings  # noqa: E402


def test_effective_uses_defaults():
    rc = SimpleNamespace(digest_filters=None)
    eff = get_effective_pipeline_settings(rc, "novost")
    assert eff["max_post_age_hours"] == 72.0
    assert eff["max_posts_per_digest"] == 3
    assert eff["text_similarity_threshold"] == 0.90
    assert eff["min_rafinad_len_similarity_dedup"] == 80


def test_by_topic_overrides_age():
    rc = SimpleNamespace(
        digest_filters={
            "defaults": {"max_post_age_hours": 72},
            "by_topic": {"sport": {"max_post_age_hours": 48}},
        }
    )
    assert get_effective_pipeline_settings(rc, "novost")["max_post_age_hours"] == 72.0
    assert get_effective_pipeline_settings(rc, "sport")["max_post_age_hours"] == 48.0
