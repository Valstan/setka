"""BulletinBuilder behaviour (empty header, group_names lookup)."""

from modules.publisher.bulletin_builder import BulletinBuilder


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
    b = BulletinBuilder(header="", hashtags=["тест"], local_hashtag="#тест", max_text_length=4096)
    r = b.build_bulletin(posts, group_names={"100": "Группа тест"})
    assert not r.text.startswith("📰")
    assert r.text.startswith("✍ ")
    assert "[https://vk.com/wall-100_1|Группа тест]" in r.text
    assert "#тест" in r.text


def test_explicit_empty_string_not_replaced_by_default_header():
    b = BulletinBuilder(header="")
    assert b.header == ""


def test_empty_header_without_hashtags_has_no_footer_tags():
    posts = [
        {
            "owner_id": -100,
            "id": 2,
            "text": "Текст без хештегов",
            "likes": {"count": 0},
            "comments": {"count": 0},
            "reposts": {"count": 0},
        }
    ]
    b = BulletinBuilder(header="", hashtags=[], local_hashtag="", max_text_length=4096)
    r = b.build_bulletin(posts, group_names={"100": "Группа тест"})
    assert "#тест" not in r.text
    assert "#" not in r.text


def _stub_post(post_id: int, text: str, *, owner_id: int = -100) -> dict:
    return {
        "owner_id": owner_id,
        "id": post_id,
        "text": text,
        "likes": {"count": 0},
        "comments": {"count": 0},
        "reposts": {"count": 0},
    }


def test_all_posts_empty_text_yields_empty_bulletin():
    """Whitespace-only text → empty BulletinResult, no header/hashtag leak."""
    posts = [_stub_post(1, "   "), _stub_post(2, ""), _stub_post(3, "\n\n")]
    b = BulletinBuilder(
        header="Физическое развитие:",
        hashtags=["спортМалмыж"],
        local_hashtag="#малмыж",
        max_text_length=4096,
    )
    r = b.build_bulletin(posts, group_names={"100": "Группа"})
    assert r.text == ""
    assert r.post_count == 0
    assert r.posts_included == []
    assert r.attachments_list == []
    # The reported regression: header + hashtags must NOT slip through alone
    assert "Физическое развитие" not in r.text
    assert "#спортМалмыж" not in r.text


def test_no_posts_fit_yields_empty_bulletin():
    """All candidate posts individually exceed max_text_length → empty bulletin, not header-only."""
    long = "Очень длинный текст. " * 200  # ~4000 chars × 3 posts > 4096 each + header
    posts = [_stub_post(i, long) for i in range(1, 4)]
    b = BulletinBuilder(
        header="📰 Заголовок",
        hashtags=["новости"],
        local_hashtag="#локал",
        max_text_length=200,  # tight cap — no post can possibly fit
    )
    r = b.build_bulletin(posts, group_names={"100": "Группа"})
    assert r.text == ""
    assert r.post_count == 0
    assert "Заголовок" not in r.text
    assert "#новости" not in r.text


def test_at_least_one_post_fits_produces_normal_bulletin():
    """Sanity: when at least one post fits, header/hashtag/body are all present."""
    posts = [_stub_post(1, "Короткий валидный текст")]
    b = BulletinBuilder(
        header="Заголовок:",
        hashtags=["тег"],
        local_hashtag="#локал",
        max_text_length=4096,
    )
    r = b.build_bulletin(posts, group_names={"100": "Группа"})
    assert r.post_count == 1
    assert "Заголовок" in r.text
    assert "Короткий валидный текст" in r.text
    assert "#тег" in r.text
