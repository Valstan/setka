"""Тесты футера сводки («Ленты соседей») — этап 4 ребрендинга."""

from modules.publisher.bulletin_builder import BulletinBuilder


def _posts(n=3, text_len=200):
    return [
        {
            "id": i,
            "owner_id": -100,
            "text": f"Пост {i} " + "х" * text_len,
            "views": {"count": 10},
        }
        for i in range(1, n + 1)
    ]


def test_footer_between_posts_and_hashtags():
    builder = BulletinBuilder(
        header="Новости:",
        hashtags=["#новости"],
        local_hashtag="#тест",
        footer="Ленты соседей: Луза ИНФО https://vk.com/luza_info43",
    )
    result = builder.build_bulletin(_posts(), group_names={})
    assert "Ленты соседей" in result.text
    lines = result.text.split("\n")
    footer_idx = next(i for i, ln in enumerate(lines) if ln.startswith("Ленты соседей"))
    hashtag_idx = next(i for i, ln in enumerate(lines) if "#новости" in ln)
    assert footer_idx < hashtag_idx  # футер до хэштегов


def test_footer_absent_when_empty():
    builder = BulletinBuilder(header="Новости:", hashtags=["#новости"], footer="")
    result = builder.build_bulletin(_posts(), group_names={})
    assert "Ленты соседей" not in result.text


def test_footer_counts_in_length_budget():
    """Футер вытесняет посты, а не вылезает за лимит длины."""
    footer = "Ленты соседей: " + "х" * 100
    builder = BulletinBuilder(header="", hashtags=[], footer=footer, max_text_length=400)
    result = builder.build_bulletin(_posts(n=5, text_len=150), group_names={})
    if result.post_count:
        assert len(result.text) <= 400
        assert footer in result.text
