"""Tests for GET /api/regions/vk-links — список сообществ сети для копирования."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from web.api import regions as regions_api


def _region(code, name, vk_group_id, kind="raion", parent_region_id=None, rid=0, neighbors=None):
    return SimpleNamespace(
        id=rid,
        code=code,
        name=name,
        kind=kind,
        vk_group_id=vk_group_id,
        parent_region_id=parent_region_id,
        is_active=True,
        center_city=None,
        neighbors=neighbors,
        config=None,
    )


def _db_returning(regions, members_rows=(), growth_rows=()):
    """Мок трёх execute подряд: регионы → (region_id, members) → строки снимков.

    Третий запрос уходит только когда в списке есть хоть одно сообщество
    (``_collect_network_growth`` выходит раньше на пустом составе), поэтому в
    side_effect он лежит последним и на пустых данных просто не забирается.
    """
    regions_result = MagicMock()
    regions_result.scalars.return_value.all.return_value = regions
    members_result = MagicMock()
    members_result.all.return_value = list(members_rows)
    growth_result = MagicMock()
    growth_result.all.return_value = list(growth_rows)
    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[regions_result, members_result, growth_result])
    return db


@pytest.mark.asyncio
async def test_vk_links_returns_blocks_and_flat_text():
    db = _db_returning(
        [
            _region("kirov_obl", "КИРОВСКАЯ ОБЛАСТЬ - ИНФО", -168170001, kind="oblast", rid=21),
            _region("mi", "МАЛМЫЖ - ИНФО", -158787639, parent_region_id=21, rid=1),
        ],
        members_rows=[(21, 677), (1, 3657)],
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["total"] == 2
    assert resp["total_members"] == 677 + 3657
    assert [b["title"] for b in resp["blocks"]] == ["Кировская область"]
    assert resp["text"].splitlines() == [
        "Кировская область:",
        "Кировская область ИНФО — 677 — https://vk.com/club168170001",
        "Малмыж ИНФО — 3657 — https://vk.com/club158787639",
    ]


@pytest.mark.asyncio
async def test_vk_links_block_carries_its_own_text():
    """У блока свой text — кнопка «копировать блок» не пересобирает его в JS."""
    db = _db_returning(
        [_region("tatarstan_obl", "ТАТАРСТАН - ИНФО", -239149826, kind="oblast", rid=22)]
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["blocks"][0]["text"] == (
        "Татарстан:\nТатарстан ИНФО — https://vk.com/club239149826"
    )


@pytest.mark.asyncio
async def test_vk_links_empty_when_nothing_published():
    resp = await regions_api.get_vk_links(db=_db_returning([]))
    assert resp["blocks"] == []
    assert resp["text"] == ""
    assert resp["total"] == 0
    assert resp["total_members"] == 0
    assert resp["neighbors"] == {}


def test_vk_links_route_declared_before_region_code_catch_all():
    """Порядок роутов: «/vk-links» обязан идти ДО «/{region_code}».

    Иначе FastAPI примет «vk-links» за код региона и эндпоинт станет
    недостижимым (та же грабля, что у «/suggest-neighbors»).
    """
    paths = [r.path for r in regions_api.router.routes]
    assert paths.index("/vk-links") < paths.index("/{region_code}")


@pytest.mark.asyncio
async def test_vk_links_ships_neighbor_graph_including_unlaunched():
    """Граф соседства едет целиком — включая район без своей группы.

    Он транзитный узел: без него «по соседству» на лендинге порвёт цепочку
    (Луза дотягивается до Нагорска только через Мураши).
    """
    db = _db_returning(
        [
            _region("kirov_obl", "КИРОВСКАЯ ОБЛАСТЬ - ИНФО", -168170001, kind="oblast", rid=21),
            _region(
                "luza",
                "ЛУЗА - ИНФО",
                -240505724,
                parent_region_id=21,
                rid=48,
                neighbors="murashi,oparino",
            ),
            _region(
                "murashi",
                "МУРАШИ - ИНФО",
                None,
                parent_region_id=21,
                rid=43,
                neighbors="luza,nagorsk",
            ),
        ]
    )

    resp = await regions_api.get_vk_links(db=db)

    # Мураши без группы — в списке их нет, а в графе есть.
    codes = [i["code"] for b in resp["blocks"] for i in b["items"]]
    assert "murashi" not in codes
    assert resp["neighbors"]["murashi"] == ["luza", "nagorsk"]
    assert resp["neighbors"]["luza"] == ["murashi", "oparino"]


# ── Прирост подписчиков (заказ владельца 2026-08-27) ───────────────────────


def _growth_db(members_rows, growth_rows):
    return _db_returning(
        [
            _region("kirov_obl", "КИРОВСКАЯ ОБЛАСТЬ - ИНФО", -168170001, kind="oblast", rid=21),
            _region("mi", "МАЛМЫЖ - ИНФО", -158787639, parent_region_id=21, rid=1),
        ],
        members_rows=members_rows,
        growth_rows=growth_rows,
    )


@pytest.mark.asyncio
async def test_vk_links_ships_growth_windows_and_months():
    yesterday = date.today() - timedelta(days=1)
    db = _growth_db(
        members_rows=[(21, 700), (1, 3700)],
        growth_rows=[
            (21, yesterday, 690),
            (1, yesterday, 3660),
            (21, date.today(), 700),
            (1, date.today(), 3700),
        ],
    )

    resp = await regions_api.get_vk_links(db=db)

    growth = resp["growth"]
    assert growth["total_members"] == 4400
    assert growth["regions_counted"] == 2
    day = next(w for w in growth["windows"] if w["key"] == "day")
    assert day["delta"] == 50
    assert [m["current"] for m in growth["months"]] == [False, False, True]


@pytest.mark.asyncio
async def test_vk_links_growth_is_none_when_only_one_day_of_snapshots():
    """Одна точка — «сколько сейчас», а не «на сколько выросли»: плашек нет."""
    db = _growth_db(
        members_rows=[(21, 700), (1, 3700)],
        growth_rows=[(21, date.today(), 700), (1, date.today(), 3700)],
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["growth"] is None


@pytest.mark.asyncio
async def test_vk_links_growth_ignores_regions_absent_from_the_list():
    """Прирост считается по показываемому составу, а не по всей таблице снимков.

    Снимки региона, которого в списке нет (деактивирован / без группы), не
    должны попадать ни в «было», ни в «стало» — иначе его отключение прочтётся
    как обвал сети.
    """
    yesterday = date.today() - timedelta(days=1)
    db = _growth_db(
        members_rows=[(21, 700), (1, 3700)],
        growth_rows=[
            (21, yesterday, 690),
            (1, yesterday, 3660),
            (99, yesterday, 5000),  # чужой регион: в блоках его нет
            (21, date.today(), 700),
            (1, date.today(), 3700),
        ],
    )

    resp = await regions_api.get_vk_links(db=db)

    assert resp["growth"]["total_members"] == 4400
    assert resp["growth"]["regions_counted"] == 2


@pytest.mark.asyncio
async def test_vk_links_response_survives_its_own_response_model():
    """Ответ обязан пройти через VkLinksResponse — иначе поле молча срежется.

    Юнит-тесты зовут хендлер напрямую, минуя ``response_model``: без этой
    проверки новое поле было бы «зелёным» здесь и отсутствовало бы на проде.
    """
    yesterday = date.today() - timedelta(days=1)
    db = _growth_db(
        members_rows=[(21, 700), (1, 3700)],
        growth_rows=[
            (21, yesterday, 690),
            (1, yesterday, 3660),
            (21, date.today(), 700),
            (1, date.today(), 3700),
        ],
    )

    resp = await regions_api.get_vk_links(db=db)
    validated = regions_api.VkLinksResponse.model_validate(resp)

    assert validated.growth is not None
    assert validated.growth.windows[0].key == "day"
    assert validated.growth.months[-1].current is True
    assert validated.total_members == 4400
