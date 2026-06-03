"""Тесты классификатора рекламы предложки."""

from __future__ import annotations

from modules.ad_cabinet.classifier import classify


async def test_plain_news_not_ad():
    is_ad, score, reasons = await classify(
        {"text": "Завтра в школе праздник, приходите всей семьёй."}
    )
    assert is_ad is False


async def test_commercial_offer_is_ad():
    is_ad, score, reasons = await classify(
        {"text": "Размещу рекламу в вашем паблике. Прайс пришлю. Пишите @reklama_agent"}
    )
    assert is_ad is True
    assert score >= 3
    assert reasons  # причины перечислены


async def test_phone_and_price_is_ad():
    is_ad, score, reasons = await classify(
        {"text": "Продаю, звоните 8 912 345 67 89, всё за 1000 руб"}
    )
    assert is_ad is True
    assert any("телефон" in r for r in reasons)


async def test_theme_reklama_does_not_short_circuit():
    # theme='reklama' должен быть выкушен (R10); VK-флаг marked_as_ads срабатывает.
    is_ad, score, reasons = await classify(
        {"text": "обычный текст", "marked_as_ads": True}, {"theme": "reklama"}
    )
    assert is_ad is True


async def test_external_link_adds_signal():
    is_ad, score, reasons = await classify(
        {"text": "Заходите на наш сайт https://example.com/shop за скидками"}
    )
    assert is_ad is True
    assert any("ссылка" in r for r in reasons)


async def test_base_score_only_ad_has_reason():
    # Базовый фильтр дайджеста ПРОПУСКАЕТ пост (его порог 4): "скидка" (2) +
    # "подробности" (1) = score 3, без предложка-сигналов (нет контактов/ссылок/
    # оффер-слов). Наш порог 3 → is_ad, но раньше reasons оставались []. Регрессия
    # на «пустые reasons_json» — карточка обязана показывать причину.
    is_ad, score, reasons = await classify({"text": "Большая скидка на товар, подробности у нас"})
    assert is_ad is True
    assert score >= 3
    assert reasons  # причина не пуста
    assert any("score" in r.lower() or "коммерческ" in r.lower() for r in reasons)
