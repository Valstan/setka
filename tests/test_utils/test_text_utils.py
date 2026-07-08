"""Tests for utils.text_utils.truncate_text.

Восстановленный F821-импорт (2026-05-22): `from utils.text_utils import
truncate_text` в modules/publisher/bulletin_builder.py пропал при автоматической
legacy-зачистке. В отличие от других F821, эта ветка ЖИВАЯ — вызывается
из BulletinBuilder.build_bulletin при превышении max_text_length.

Тесты фиксируют contract truncate_text. Существующий test_bulletin_builder.py
не покрывает truncation-ветку (`max_text_length=4096` — никогда не достигается
тестовыми данными).
"""

from __future__ import annotations

import pytest

from utils.text_utils import is_advertisement, is_hard_spam, truncate_text


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


def test_truncate_text_used_by_bulletin_builder_bezfoto_branch():
    """Integration: вызов truncate_text из bulletin_builder.py:434 — это
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


# ───────── is_advertisement: намерение купли-продажи товара (2026-07-07) ─────────


@pytest.mark.parametrize(
    "text",
    [
        "ПРОДАМ ВАЗ 2104 карбюратор 2003г.в на ходу с документами 3 хозяина",
        "Продам практически новый велотренажёр",
        "Куплю косилку КРР и запчасти от неё",
        "Продаётся мебель в связи с продажей дома",
        "Продаю ниву инжектор, обменяю на что-то",
    ],
)
def test_is_advertisement_catches_private_goods_sale(text):
    # Раньше эти тексты (без цены/телефона) проходили как «не реклама» и утекали в
    # сводку. Теперь глагол купли-продажи товара → сразу реклама (граница score>=4).
    assert is_advertisement(text, theme="novost") is True


@pytest.mark.parametrize(
    "text",
    [
        "Оказываю правовое сопровождение по вопросам банкротства физических лиц",
        "Проведение весёлых юбилеев, свадеб, встреч выпускников",
        "Ремонт квартир под ключ, качественно",
    ],
)
def test_is_advertisement_leaves_local_services(text):
    # Услуги мастеров (иные глаголы) НЕ ловятся сюда → остаются оператору (hold),
    # как решил владелец (градация коммерции). Проверяем, что глаголы продажи товара
    # их не задевают.
    assert is_advertisement(text, theme="novost") is False


def test_is_advertisement_sale_verb_word_boundary():
    # «распродам»/«распродажа» — не частная продажа товара по глаголу; \b не матчит.
    assert is_advertisement("мы распродам остатки скоро", theme="novost") is False


def test_is_advertisement_skips_reklama_theme():
    # Для темы reklama детектор рекламы не срабатывает (реклама там ожидаема).
    assert is_advertisement("Продам ВАЗ 2104", skip_for_reklama=True, theme="reklama") is False


# ───────── is_hard_spam: скам/увод в обход VK (инцидент Уржум 2026-07-08) ─────────


def test_is_hard_spam_catches_incident_job_scam():
    # Точный фрагмент, из-за которого VK забанил аккаунт: «удалённая работа /
    # рассылка рекламы / по готовой системе». Ловится (несколькими маркерами).
    text = (
        "Удалённая работа через интернет. Гибкий график. Бесплатное обучение. "
        "Работа по готовой системе. Обязанности: рассылка рекламы и ответы на "
        "сообщения. Подробности по ссылке."
    )
    assert is_hard_spam(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "Удалённая работа на дому, доход от 50000 в месяц",
        "Работа в интернете, гибкий график",
        "Рассылка рекламы — платим сразу",
        "Заработок от 3000 рублей в день без вложений",
        "Пассивный доход, финансовая независимость",
        "Ставки на спорт, казино онлайн 1xbet",
        "Займ без отказа на карту за 5 минут",
        "Кредит без справок и залога",
        "Инвестиции в криптовалюту, бинарные опционы",
    ],
)
def test_is_hard_spam_catches_scam_markers(text):
    assert is_hard_spam(text) is True


def test_is_hard_spam_messenger_funnel_three_plus():
    # ≥3 разных мессенджера-воронки в одном посте = скам-контакт (как в инциденте:
    # «Wa, Tg, Max +7…»). Один-два — легальный локальный контакт, НЕ спам.
    assert is_hard_spam("Пишите: Wa, Tg, Max +79210169384") is True
    assert is_hard_spam("Звоните или напишите в WhatsApp") is False
    assert is_hard_spam("Telegram или Viber для связи") is False


@pytest.mark.parametrize(
    "text",
    [
        "Продам велосипед, колёса 16 дюймов. Состояние новое. Цена 3500 р.",
        "ПРОДАМ ВАЗ 2104 карбюратор 2003г.в на ходу с документами",
        "Куплю косилку КРР и запчасти",
        "Оказываю правовое сопровождение по вопросам банкротства физлиц",
        "Ремонт квартир под ключ, качественно, звоните",
        "Проведение юбилеев и свадеб, тамада",
    ],
)
def test_is_hard_spam_leaves_legit_local_ads(text):
    # Легальные частные объявления и услуги мастеров остаются в рекламной рубрике:
    # is_hard_spam режет только скам/увод, не трогая доску объявлений.
    assert is_hard_spam(text) is False


def test_is_hard_spam_empty():
    assert is_hard_spam("") is False
    assert is_hard_spam(None) is False  # type: ignore[arg-type]
