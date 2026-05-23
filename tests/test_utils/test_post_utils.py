"""Tests for post_utils attribution (VK wiki links)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.post_utils import extract_source_attribution  # noqa: E402


def test_extract_source_attribution_wiki_link_with_group_name():
    post = {"owner_id": -123456, "id": 789}
    s = extract_source_attribution(post, "Новости района")
    assert s == "[https://vk.com/wall-123456_789|Новости района]"


def test_extract_source_attribution_escapes_pipe_in_name():
    post = {"owner_id": -1, "id": 2}
    s = extract_source_attribution(post, "A|B")
    assert s == "[https://vk.com/wall-1_2|A·B]"


def test_extract_source_attribution_fallback_label():
    post = {"owner_id": 100, "id": 5}
    s = extract_source_attribution(post, "")
    assert s == "[https://vk.com/wall100_5|Источник]"
