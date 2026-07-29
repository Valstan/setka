"""Тесты modules/region_links.py — список сообществ сети для рассылки людям.

Проверяем ровно то, что видит человек в скопированном тексте: короткое имя,
ссылку, группировку по областям и порядок строк. Данные — реальные формы имён
из прод-БД («МАЛМЫЖ - ИНФО», «Тужа, Кировская область», «КИРОВО-ЧЕПЕЦК - ИНФО»).
"""

from __future__ import annotations

from types import SimpleNamespace

from modules.region_links import (
    base_title,
    build_blocks,
    build_neighbor_graph,
    community_url,
    parse_neighbors,
    render_block_text,
    render_item_line,
    render_text,
    short_name,
)


def _region(
    code: str,
    name: str,
    vk_group_id=None,
    kind: str = "raion",
    parent_region_id=None,
    is_active: bool = True,
    center_city=None,
    rid: int = 0,
):
    return SimpleNamespace(
        id=rid or abs(hash(code)) % 10000,
        code=code,
        name=name,
        kind=kind,
        vk_group_id=vk_group_id,
        parent_region_id=parent_region_id,
        is_active=is_active,
        center_city=center_city,
        neighbors=None,
        config=None,
    )


# --- ссылка -----------------------------------------------------------------


def test_community_url_from_negative_owner_id():
    assert community_url(-158787639) == "https://vk.com/club158787639"


def test_community_url_tolerates_positive_id():
    """В БД один регион (tuzha) исторически лежал положительным — ссылка та же."""
    assert community_url(239050321) == "https://vk.com/club239050321"


# --- имена ------------------------------------------------------------------


def test_short_name_from_caps_info_name():
    assert short_name("МАЛМЫЖ - ИНФО") == "Малмыж ИНФО"


def test_short_name_keeps_two_word_proper_names():
    assert short_name("ВЯТСКИЕ ПОЛЯНЫ - ИНФО") == "Вятские Поляны ИНФО"


def test_short_name_keeps_hyphenated_city():
    assert short_name("КИРОВО-ЧЕПЕЦК - ИНФО") == "Кирово-Чепецк ИНФО"


def test_short_name_lowercases_geo_common_noun():
    """«ОБЛАСТЬ» — нарицательное: «Кировская область», не «Кировская Область»."""
    assert short_name("КИРОВСКАЯ ОБЛАСТЬ - ИНФО") == "Кировская область ИНФО"


def test_short_name_from_free_form_name():
    """Имя не по шаблону: отрезаем гео-хвост после запятой, регистр не трогаем."""
    assert short_name("Тужа, Кировская область") == "Тужа ИНФО"


def test_short_name_without_dash_before_suffix():
    assert short_name("НЕМА ИНФО") == "Нема ИНФО"


def test_base_title_drops_info_suffix():
    assert base_title("КИРОВСКАЯ ОБЛАСТЬ - ИНФО") == "Кировская область"


# --- группировка ------------------------------------------------------------


def _sample_regions():
    kirov = _region("kirov_obl", "КИРОВСКАЯ ОБЛАСТЬ - ИНФО", -168170001, kind="oblast", rid=21)
    tatarstan = _region("tatarstan_obl", "ТАТАРСТАН - ИНФО", -239149826, kind="oblast", rid=22)
    return [
        kirov,
        tatarstan,
        _region("mi", "МАЛМЫЖ - ИНФО", -158787639, parent_region_id=21, rid=1),
        _region("klz", "КИЛЬМЕЗЬ - ИНФО", -168172770, parent_region_id=21, rid=7),
        _region("kumyony", "КУМЁНЫ - ИНФО", -221492355, parent_region_id=21, rid=27),
        _region("bal", "БАЛТАСИ - ИНФО", -179203620, parent_region_id=22, rid=14),
    ]


def test_blocks_grouped_by_oblast():
    blocks = build_blocks(_sample_regions())
    assert [b["title"] for b in blocks] == ["Кировская область", "Татарстан"]


def test_oblast_group_goes_first_in_its_block():
    blocks = build_blocks(_sample_regions())
    kirov = blocks[0]
    assert kirov["items"][0]["name"] == "Кировская область ИНФО"
    assert kirov["items"][0]["kind"] == "oblast"


def test_raions_sorted_alphabetically_with_yo_normalized():
    """«Кумёны» сортируется как «Кумены» — иначе «ё» уезжает за «я»."""
    blocks = build_blocks(_sample_regions())
    names = [i["name"] for i in blocks[0]["items"][1:]]
    assert names == ["Кильмезь ИНФО", "Кумёны ИНФО", "Малмыж ИНФО"]


def test_region_without_vk_group_is_skipped():
    """Заготовка района (группа ещё не создана) читателю бесполезна."""
    regions = _sample_regions() + [
        _region("yurya", "ЮРЬЯ - ИНФО", None, parent_region_id=21, is_active=False, rid=42)
    ]
    codes = [i["code"] for b in build_blocks(regions) for i in b["items"]]
    assert "yurya" not in codes


def test_inactive_region_is_skipped():
    regions = _sample_regions() + [
        _region("test", "Тест-Инфо", -137760500, is_active=False, rid=15)
    ]
    codes = [i["code"] for b in build_blocks(regions) for i in b["items"]]
    assert "test" not in codes


def test_orphan_region_goes_to_other_block_last():
    regions = _sample_regions() + [
        _region("solo", "ОДИНОКИЙ - ИНФО", -111, parent_region_id=None, rid=99)
    ]
    blocks = build_blocks(regions)
    assert blocks[-1]["title"] == "Другие"
    assert [i["code"] for i in blocks[-1]["items"]] == ["solo"]


def test_empty_oblast_produces_no_block():
    """Область без единой готовой группы района — блок из одной себя, не пустой."""
    lonely = _region("obl_x", "ПУСТАЯ ОБЛАСТЬ - ИНФО", -5, kind="oblast", rid=77)
    blocks = build_blocks([lonely])
    assert len(blocks) == 1 and len(blocks[0]["items"]) == 1


# --- подписчики и красивый адрес ---------------------------------------------


def test_item_line_with_members():
    """Формат владельца: «Имя ИНФО — 3657 — ссылка»."""
    blocks = build_blocks(_sample_regions(), members={1: 3657})
    line = [render_item_line(i) for b in blocks for i in b["items"] if i["code"] == "mi"][0]
    assert line == "Малмыж ИНФО — 3657 — https://vk.com/club158787639"


def test_item_line_without_snapshot_has_no_number():
    """Региону без снапшота (добавлен сегодня) число не рисуем — и не ноль."""
    blocks = build_blocks(_sample_regions())
    line = [render_item_line(i) for b in blocks for i in b["items"] if i["code"] == "mi"][0]
    assert line == "Малмыж ИНФО — https://vk.com/club158787639"


def test_screen_name_preferred_over_club_id():
    region = _region("mi", "МАЛМЫЖ - ИНФО", -158787639, rid=1)
    region.config = {"screen_name": "malmyzh_info"}
    blocks = build_blocks([region])
    assert blocks[-1]["items"][0]["url"] == "https://vk.com/malmyzh_info"


def test_community_url_with_screen_name():
    assert community_url(-158787639, "malmyzh_info") == "https://vk.com/malmyzh_info"


# --- текст ------------------------------------------------------------------


def test_block_text_shape():
    blocks = build_blocks(_sample_regions(), members={22: 677, 14: 1431})
    text = render_block_text(blocks[1])
    assert text.splitlines() == [
        "Татарстан:",
        "Татарстан ИНФО — 677 — https://vk.com/club239149826",
        "Балтаси ИНФО — 1431 — https://vk.com/club179203620",
    ]


def test_full_text_separates_blocks_with_blank_line():
    text = render_text(build_blocks(_sample_regions()))
    assert "\n\nТатарстан:\n" in text


def test_text_has_bare_urls_for_vk_autolinking():
    """VK делает кликабельным только «голый» URL — без markdown и скобок."""
    text = render_text(build_blocks(_sample_regions()))
    for line in text.splitlines():
        if "vk.com" in line:
            assert line.endswith("https://vk.com/club" + line.rsplit("club", 1)[1])
            assert "[" not in line and "(" not in line


# --- граф соседства (сортировка «по соседству» на лендинге) -------------------


def test_parse_neighbors_splits_and_normalizes():
    assert parse_neighbors("bal, klz ;MI") == ["bal", "klz", "mi"]
    assert parse_neighbors(None) == []
    assert parse_neighbors("") == []


def test_neighbor_graph_includes_regions_without_group():
    """Заготовка без группы обязана остаться в графе транзитным узлом.

    Выкинь её — и цепочка соседства рвётся: Луза дотягивается до Нагорска
    только через Мураши, у которых своей группы пока нет.
    """
    luza = _region("luza", "ЛУЗА - ИНФО", -240505724, rid=48)
    luza.neighbors = "murashi,oparino"
    murashi = _region("murashi", "МУРАШИ - ИНФО", None, is_active=False, rid=43)
    murashi.neighbors = "luza,nagorsk"

    graph = build_neighbor_graph([luza, murashi])

    assert graph["murashi"] == ["luza", "nagorsk"]
    assert graph["luza"] == ["murashi", "oparino"]


def test_item_carries_ready_line_and_oblast():
    """У строки есть готовый text (line) и имя области — клиент пересортировывает
    порядок, но формат строки остаётся серверным."""
    blocks = build_blocks(_sample_regions(), members={1: 3657})
    item = [i for b in blocks for i in b["items"] if i["code"] == "mi"][0]
    assert item["line"] == "Малмыж ИНФО — 3657 — https://vk.com/club158787639"
    assert item["oblast"] == "Кировская область"
