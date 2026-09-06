"""Поисковый индекс покрывает все группы проекта, а не только районы.

Заказ владельца 2026-09-06: «распространи фишку с поисковым индексом на все
группы проекта». «Все» — это 41 район **и 2 области**; молчаливое сужение до
районов оставило бы области без индекса ровно так же, как их до сих пор не брал
``load_targets`` (там был жёсткий ``kind='raion'``).

У области места другого рода: не сёла, а районы. Роль в описании та же —
длинный хвост запросов «<место> новости», — поэтому строка одна, а подпись
разная.
"""

import pytest

from modules.promotion.copy import (
    _PLACES_LABEL_DISTRICTS,
    _PLACES_LABEL_LOCALITIES,
    render_group_description,
    render_search_tail,
)


def test_raion_lists_settlements():
    tail = render_search_tail(city="Тужа", localities=["Ныр", "Шешурга"])
    assert tail.startswith("Тужа — новости,")
    assert f"{_PLACES_LABEL_LOCALITIES}: Ныр, Шешурга." in tail


def test_oblast_lists_districts():
    tail = render_search_tail(
        city="Кировская область",
        localities=["Малмыж", "Уржум", "Кильмезь"],
        places_label=_PLACES_LABEL_DISTRICTS,
    )
    assert f"{_PLACES_LABEL_DISTRICTS}: Малмыж, Уржум, Кильмезь." in tail
    assert _PLACES_LABEL_LOCALITIES not in tail


def test_label_reaches_the_description():
    text = render_group_description(
        district_name="Кировской области",
        center_city="Кировская область",
        localities=["Малмыж"],
        places_label=_PLACES_LABEL_DISTRICTS,
    )
    assert "Районы и округа: Малмыж." in text


@pytest.mark.parametrize("label", [_PLACES_LABEL_LOCALITIES, _PLACES_LABEL_DISTRICTS])
def test_cap_applies_to_both_kinds(label):
    """Восемь — потолок для любого вида мест: длинный список читается как спам."""
    tail = render_search_tail(
        city="X", localities=[f"Место{i}" for i in range(30)], places_label=label
    )
    assert "Место8" not in tail
