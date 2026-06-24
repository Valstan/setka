"""Tests for utils.text_utils.truncate_text.

Восстановленный F821-импорт (2026-05-22): `from utils.text_utils import
truncate_text` в modules/publisher/digest_builder.py пропал при автоматической
legacy-зачистке. В отличие от других F821, эта ветка ЖИВАЯ — вызывается
из BulletinBuilder.build_bulletin при превышении max_text_length.

Тесты фиксируют contract truncate_text. Существующий test_digest_builder.py
не покрывает truncation-ветку (`max_text_length=4096` — никогда не достигается
тестовыми данными).
"""

from __future__ import annotations

from utils.text_utils import truncate_text


def test_truncate_text_short_text_unchanged():
    """text ≤ max_length → возвращается как есть, без suffix."""
    assert truncate_text("short", 10) == "short"
    assert truncate_text("exactly20chars__here", 20) == "exactly20chars__here"


def test_truncate_text_empty_string_unchanged():
    """Пустая строка → пустая строка (truthy-check предохраняет от индексирования)."""
    assert truncate_text("", 10) == ""


def test_truncate_text_long_text_truncated_with_default_suffix():
    """text > max_length → обрезка + `...` (default suffix). Длина итога = max_length."""
    text = "a" * 100
    result = truncate_text(text, max_length=10)

    assert result.endswith("...")
    assert len(result) == 10
    assert result == "aaaaaaa..."


def test_truncate_text_custom_suffix():
    """Кастомный suffix используется при truncation."""
    text = "a" * 100
    result = truncate_text(text, max_length=20, suffix="\n\n...")

    assert result.endswith("\n\n...")
    assert len(result) == 20


def test_truncate_text_used_by_digest_builder_bezfoto_branch():
    """Integration: вызов truncate_text из digest_builder.py:434 — это
    `TextOnlyBulletinBuilder.build_bezfoto_bulletin`, активный метод publisher'а
    (используется для рекламных сводок, migrated from old_postopus
    `post_bezfoto()`). Длинные text_items + маленький max_text_length →
    truncation срабатывает, итог не превышает лимит, маркер `\\n\\n...`
    присутствует. Покрывает live-ветку F821-импорта."""
    from modules.publisher.bulletin_builder import TextOnlyBulletinBuilder

    items = ["Длинная новость с множеством слов и подробностей. " * 5] * 10
    builder = TextOnlyBulletinBuilder(
        header="📰 Тест",
        hashtags=["тест"],
        local_hashtag="#тест",
        max_text_length=200,
    )
    result = builder.build_bezfoto_bulletin(
        text_items=items, header="📰 Test header", hashtag="test"
    )

    assert len(result.text) <= 200
    assert "\n\n..." in result.text
