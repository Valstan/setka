"""DigestBuilder behaviour (empty header, group_names lookup)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from modules.publisher.digest_builder import DigestBuilder


def test_empty_header_starts_with_post_marker_not_default_title():
    posts = [
        {
            "owner_id": -100,
            "id": 1,
            "text": "Текст новости",
            "likes": {"count": 0},
            "comments": {"count": 0},
            "reposts": {"count": 0},
        }
    ]
    b = DigestBuilder(header="", hashtags=["тест"], local_hashtag="#тест", max_text_length=4096)
    r = b.build_digest(posts, group_names={"100": "Группа тест"})
    assert not r.text.startswith("📰")
    assert r.text.startswith("✍ ")
    assert "[https://vk.com/wall-100_1|Группа тест]" in r.text
    assert "#тест" in r.text


def test_explicit_empty_string_not_replaced_by_default_header():
    b = DigestBuilder(header="")
    assert b.header == ""
