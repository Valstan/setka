"""Тесты хедлайнера (modules/publisher/headliner.py)."""

from modules.publisher.headliner import (
    MAX_LEN,
    MIN_LEN,
    MIN_POOL,
    build_headliner,
    headliner_enabled,
    pick_headliner,
)


def _post(pid=1, text="х" * 200, views=100, likes=5, comments=1, reposts=0, **extra):
    return {
        "id": pid,
        "owner_id": -111,
        "text": text,
        "views": {"count": views},
        "likes": {"count": likes},
        "comments": {"count": comments},
        "reposts": {"count": reposts},
        **extra,
    }


def test_enabled_by_default_and_opt_out():
    assert headliner_enabled(None)
    assert headliner_enabled({})
    assert headliner_enabled({"headliner": True})
    assert not headliner_enabled({"headliner": False})


def test_small_pool_no_headliner():
    posts = [_post(pid=i) for i in range(MIN_POOL - 1)]
    assert pick_headliner(posts) is None


def test_picks_highest_rated_in_range():
    weak = _post(pid=1, likes=0, views=500)
    strong = _post(pid=2, likes=50, comments=10, views=500)
    other = _post(pid=3, likes=1, views=500)
    assert pick_headliner([weak, strong, other])["id"] == 2


def test_skips_out_of_range_text():
    """Простыня и обрывок не годятся, даже если рейтинг у них выше."""
    longread = _post(pid=1, text="х" * (MAX_LEN + 1), likes=999)
    stub = _post(pid=2, text="х" * (MIN_LEN - 1), likes=999)
    normal = _post(pid=3, likes=1)
    assert pick_headliner([longread, stub, normal])["id"] == 3


def test_unmeasured_views_not_headliner():
    """Пост без просмотров (rating=None) не может стать хедлайнером."""
    unmeasured = _post(pid=1, views=None, likes=999)
    unmeasured["views"] = None
    normal = _post(pid=2, likes=1)
    third = _post(pid=3, likes=0)
    assert pick_headliner([unmeasured, normal, third])["id"] == 2


def test_all_out_of_range_returns_none():
    posts = [_post(pid=i, text="х" * 10) for i in range(5)]
    assert pick_headliner(posts) is None


def test_build_headliner_text_and_attachments():
    post = _post(
        text="Важная новость района.",
        attachments=[{"type": "photo", "photo": {"owner_id": -111, "id": 9, "access_key": "k"}}],
    )
    text, atts = build_headliner(post, group_name="Родник", local_hashtag="#опарино")
    assert text.startswith("Важная новость района.")
    assert "#опарино" in text
    assert "Новости" not in text.split("\n")[0]  # без шапки-заголовка сводки
    assert atts and atts[0].startswith("photo-111_9")


def test_build_headliner_hide_attribution():
    post = _post(
        text="Текст новости для проверки атрибуции и её отключения тут.", hide_attribution=True
    )
    text, _ = build_headliner(post, group_name="Родник", local_hashtag="")
    assert "Родник" not in text and "vk.com" not in text
