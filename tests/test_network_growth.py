"""Тесты арифметики прироста сети (modules/network_growth.py).

Без БД и VK: на вход строки снимков ``(region_id, snapshot_date,
members_count)``, на выход — структура для лендинга. Проверяем ровно те
свойства, ради которых модуль написан: перенос значения вперёд через дыру в
сборе, отделение новых сообществ от выросших, честная деградация окна, которое
не помещается в историю, и границы месяцев (включая стык года).
"""

from datetime import date

import pytest

from modules.network_growth import (
    build_growth,
    format_day_ru,
    growth_query_start,
    index_snapshots,
    month_title_ru,
    plural_ru,
    region_ids_from_blocks,
    total_as_of,
)


def _rows(*items):
    """(region_id, 'YYYY-MM-DD', members) → строки снимков."""
    return [(rid, date.fromisoformat(day), count) for rid, day, count in items]


def _window(growth, key):
    for window in growth["windows"]:
        if window["key"] == key:
            return window
    return None


def _month(growth, key):
    for entry in growth["months"]:
        if entry["key"] == key:
            return entry
    return None


# ── Вспомогательные форматтеры ─────────────────────────────────────────────


def test_plural_ru_covers_russian_forms():
    assert plural_ru(1, "день", "дня", "дней") == "день"
    assert plural_ru(2, "день", "дня", "дней") == "дня"
    assert plural_ru(5, "день", "дня", "дней") == "дней"
    assert plural_ru(11, "день", "дня", "дней") == "дней"
    assert plural_ru(21, "день", "дня", "дней") == "день"


def test_format_day_ru_uses_genitive_month():
    assert format_day_ru(date(2026, 6, 8)) == "8 июня"


def test_month_title_shows_year_only_for_other_years():
    assert month_title_ru(2026, 7, 2026) == "июль"
    assert month_title_ru(2025, 12, 2026) == "декабрь 2025"


def test_growth_query_start_reaches_earliest_needed_month():
    # Полугодовое окно от 2026-08-27 глубже, чем начало июня, — берётся оно.
    assert growth_query_start(date(2026, 8, 27)) == date(2026, 2, 27)


# ── Перенос вперёд ─────────────────────────────────────────────────────────


def test_total_as_of_carries_last_known_value_forward():
    """День без снимка не обнуляет регион — берётся последний известный."""
    indexed = index_snapshots(_rows((1, "2026-08-01", 100), (1, "2026-08-05", 130)))
    assert total_as_of(indexed, date(2026, 8, 3)) == (100, {1})
    assert total_as_of(indexed, date(2026, 8, 5)) == (130, {1})


def test_total_as_of_ignores_region_before_its_first_snapshot():
    indexed = index_snapshots(_rows((1, "2026-08-10", 100)))
    total, counted = total_as_of(indexed, date(2026, 8, 9))
    assert (total, counted) == (0, set())


def test_index_snapshots_filters_to_displayed_regions():
    """Деактивированный регион не должен участвовать в «было»."""
    indexed = index_snapshots(_rows((1, "2026-08-01", 100), (2, "2026-08-01", 50)), region_ids=[1])
    assert set(indexed) == {1}


def test_index_snapshots_skips_broken_rows():
    indexed = index_snapshots(
        [(1, None, 10), (None, date(2026, 8, 1), 10), (1, date(2026, 8, 1), None)]
    )
    assert indexed == {}


def test_index_snapshots_accepts_iso_strings():
    indexed = index_snapshots([(1, "2026-08-01", 100)])
    assert indexed[1] == ([date(2026, 8, 1)], [100])


# ── Пустые и вырожденные данные ────────────────────────────────────────────


def test_build_growth_returns_none_without_data():
    assert build_growth([], today=date(2026, 8, 27)) is None


def test_build_growth_returns_none_on_single_day():
    """Одна точка — это «сколько сейчас», а не «на сколько выросли»."""
    rows = _rows((1, "2026-08-27", 100))
    assert build_growth(rows, today=date(2026, 8, 27)) is None


# ── Окно «за сутки» ────────────────────────────────────────────────────────


def test_day_window_measures_last_two_days():
    rows = _rows((1, "2026-08-25", 100), (1, "2026-08-26", 110), (1, "2026-08-27", 125))
    growth = build_growth(rows, today=date(2026, 8, 27))
    day = _window(growth, "day")
    assert day["delta"] == 15
    assert day["days"] == 1
    assert day["note"] == ""


def test_day_window_spans_gap_and_says_so():
    """Дыра в сборе: «сутки» стали двумя днями — это должно быть написано."""
    rows = _rows((1, "2026-08-25", 100), (1, "2026-08-27", 130))
    growth = build_growth(rows, today=date(2026, 8, 27))
    day = _window(growth, "day")
    assert day["delta"] == 30
    assert day["days"] == 2
    assert "фактически за 2 дня" in day["note"]


def test_day_window_sums_all_regions():
    rows = _rows(
        (1, "2026-08-26", 100),
        (2, "2026-08-26", 200),
        (1, "2026-08-27", 105),
        (2, "2026-08-27", 210),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _window(growth, "day")["delta"] == 15


# ── Окна 30 дней / полгода ─────────────────────────────────────────────────


def test_month_window_uses_thirty_days_back():
    rows = _rows(
        (1, "2026-06-08", 1000),
        (1, "2026-07-28", 1200),
        (1, "2026-08-26", 1290),
        (1, "2026-08-27", 1300),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    month = _window(growth, "month")
    assert month["from_date"] == "2026-07-28"
    assert month["delta"] == 100
    assert month["partial"] is False
    assert month["title"] == "за 30 дней"


def test_half_year_window_degrades_to_all_time_with_honest_title():
    """Истории 81 день — «за полгода» превращается в «за всё время (с 8 июня)»."""
    rows = _rows((1, "2026-06-08", 1000), (1, "2026-07-28", 1200), (1, "2026-08-27", 1300))
    growth = build_growth(rows, today=date(2026, 8, 27))
    half = _window(growth, "half_year")
    assert half["partial"] is True
    assert half["title"] == "за всё время (с 8 июня)"
    assert half["from_date"] == "2026-06-08"
    assert half["delta"] == 300
    assert "данные с 8 июня" in half["note"]


def test_half_year_window_is_real_when_history_is_deep_enough():
    rows = _rows((1, "2026-01-01", 500), (1, "2026-02-27", 800), (1, "2026-08-27", 1300))
    growth = build_growth(rows, today=date(2026, 8, 27))
    half = _window(growth, "half_year")
    assert half["partial"] is False
    assert half["title"] == "за полгода"
    # 180 дней назад от 27 августа — 28 февраля; снимок 27-го переносится вперёд.
    assert half["from_date"] == "2026-02-28"
    assert half["delta"] == 500


def test_collapsed_windows_are_not_duplicated():
    """Короткая история: 30 дней и полгода — одно и то же окно, плашка одна."""
    rows = _rows((1, "2026-08-25", 100), (1, "2026-08-26", 110), (1, "2026-08-27", 120))
    growth = build_growth(rows, today=date(2026, 8, 27))
    keys = [w["key"] for w in growth["windows"]]
    assert keys == ["day", "month"]
    assert _window(growth, "month")["title"] == "за всё время (с 25 августа)"


def test_sparse_history_does_not_pass_a_month_off_as_a_day():
    """Если предыдущий снимок ровно на границе 30 дней, «сутки» это признают.

    Плашка остаётся одна (вторая — дубль по границам), поэтому единственное,
    что защищает читателя от «+100 за сутки» вместо «+100 за месяц», — подпись.
    """
    rows = _rows((1, "2026-07-28", 1000), (1, "2026-08-27", 1100))
    growth = build_growth(rows, today=date(2026, 8, 27))
    day = _window(growth, "day")
    # Все три окна схлопнулись в одни и те же границы — плашка остаётся одна.
    assert [w["key"] for w in growth["windows"]] == ["day"]
    assert day["days"] == 30
    assert "фактически за 30 дней" in day["note"]


# ── Новые сообщества внутри окна ───────────────────────────────────────────


def test_new_community_counted_separately_from_organic_growth():
    """Район, подключённый в середине окна, весь входит в прирост — с пометкой."""
    rows = _rows(
        (1, "2026-07-28", 1000),
        (1, "2026-08-27", 1050),  # органический рост: +50
        (2, "2026-08-20", 700),  # новый район
        (2, "2026-08-27", 720),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    month = _window(growth, "month")
    assert month["delta"] == 770  # 50 органики + 720 нового
    assert month["new_communities"] == 1
    assert month["new_members"] == 720
    assert "включая 1 новое сообщество (+720)" in month["note"]


def test_region_present_at_both_ends_is_not_new():
    rows = _rows((1, "2026-07-28", 1000), (1, "2026-08-26", 1090), (1, "2026-08-27", 1100))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _window(growth, "month")["new_communities"] == 0


def test_negative_growth_is_reported_as_is():
    rows = _rows((1, "2026-08-26", 1000), (1, "2026-08-27", 980))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _window(growth, "day")["delta"] == -20


# ── Полоса месяцев ─────────────────────────────────────────────────────────


def test_months_cover_current_and_two_previous():
    rows = _rows((1, "2026-06-08", 1000), (1, "2026-08-27", 1300))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert [m["key"] for m in growth["months"]] == ["2026-06", "2026-07", "2026-08"]
    assert [m["title"] for m in growth["months"]] == ["июнь", "июль", "август"]
    assert [m["current"] for m in growth["months"]] == [False, False, True]


def test_month_delta_measured_between_month_boundaries():
    rows = _rows(
        (1, "2026-06-30", 1000),
        (1, "2026-07-15", 1080),
        (1, "2026-07-31", 1150),
        (1, "2026-08-27", 1300),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _month(growth, "2026-07")["delta"] == 150  # 1150 − 1000
    assert _month(growth, "2026-08")["delta"] == 150  # 1300 − 1150


def test_first_month_is_marked_partial_when_history_starts_mid_month():
    rows = _rows((1, "2026-06-08", 1000), (1, "2026-06-30", 1100), (1, "2026-08-27", 1300))
    growth = build_growth(rows, today=date(2026, 8, 27))
    june = _month(growth, "2026-06")
    assert june["partial"] is True
    assert june["delta"] == 100  # с 8-го, а не с 1-го
    assert "с 8 июня" in june["note"]


def test_current_month_says_it_is_not_over():
    rows = _rows((1, "2026-07-31", 1000), (1, "2026-08-27", 1200))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert "месяц не закончился" in _month(growth, "2026-08")["note"]


def test_month_without_any_data_has_no_delta():
    """Месяц целиком раньше первого снимка — «—», а не «+0»."""
    rows = _rows((1, "2026-08-26", 1000), (1, "2026-08-27", 1100))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _month(growth, "2026-06")["delta"] is None
    assert _month(growth, "2026-07")["delta"] is None


def test_months_roll_over_the_year_boundary():
    rows = _rows((1, "2025-11-01", 900), (1, "2026-01-15", 1200))
    growth = build_growth(rows, today=date(2026, 1, 15))
    assert [m["key"] for m in growth["months"]] == ["2025-11", "2025-12", "2026-01"]
    assert [m["title"] for m in growth["months"]] == ["ноябрь 2025", "декабрь 2025", "январь"]


def test_month_bounds_do_not_leak_into_next_month():
    """Снимок 1 августа не должен попасть в июльскую дельту."""
    rows = _rows(
        (1, "2026-07-31", 1000),
        (1, "2026-08-01", 1500),
        (1, "2026-08-27", 1600),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert _month(growth, "2026-07")["to_date"] == "2026-07-31"
    assert _month(growth, "2026-08")["delta"] == 600


# ── Итоговая шапка ─────────────────────────────────────────────────────────


def test_totals_and_staleness_reported():
    """Сборщик встал два дня назад: числа на странице надо читать «на дату»."""
    rows = _rows(
        (1, "2026-08-24", 90),
        (2, "2026-08-24", 190),
        (1, "2026-08-25", 100),
        (2, "2026-08-25", 200),
    )
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert growth["total_members"] == 300
    assert growth["regions_counted"] == 2
    assert growth["latest_date"] == "2026-08-25"
    assert growth["stale_days"] == 2
    assert growth["first_date_human"] == "24 августа"


def test_fresh_data_has_no_stale_days():
    rows = _rows((1, "2026-08-26", 100), (1, "2026-08-27", 110))
    growth = build_growth(rows, today=date(2026, 8, 27))
    assert growth["stale_days"] == 0


# ── Хелперы для страницы ───────────────────────────────────────────────────


def test_region_ids_from_blocks_maps_codes():
    blocks = [{"items": [{"code": "mi"}, {"code": "ur"}]}, {"items": [{"code": "gone"}]}]
    assert region_ids_from_blocks(blocks, {"mi": 1, "ur": 2}) == [1, 2]


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 8, 27), ["2026-06", "2026-07", "2026-08"]),
        (date(2026, 9, 1), ["2026-07", "2026-08", "2026-09"]),
        (date(2027, 1, 3), ["2026-11", "2026-12", "2027-01"]),
    ],
)
def test_month_strip_shifts_with_the_calendar(today, expected):
    """Полоса едет по календарю сама — без правок кода в начале месяца."""
    rows = _rows((1, "2026-01-01", 100), (1, "2026-08-27", 200))
    growth = build_growth(rows, today=today)
    assert [m["key"] for m in growth["months"]] == expected
