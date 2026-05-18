"""
Tests for modules.filters.regional.RegionalRelevanceFilter — DB-backed
региональный фильтр.

Все обращения к SQLAlchemy мокаются (AsyncMock + side_effect).
"""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from database.models import Region
from database.models_extended import RegionConfig
from modules.filters.regional import RegionalRelevanceFilter


def _build_region(region_id: int = 1, code: str = "mi", name: str = "МАЛМЫЖ - ИНФО") -> Region:
    return Region(id=region_id, code=code, name=name, is_active=True)


def _build_session(*scalars):
    """Создать AsyncMock-сессию, последовательно возвращающую scalars().

    Каждый аргумент — то, что отдаст ``scalar_one_or_none()`` на очередной
    ``session.execute(...)``. Это нужно, потому что фильтр делает до двух
    запросов: Region и RegionConfig.
    """
    session = AsyncMock()
    results = []
    for value in scalars:
        result = MagicMock()
        result.scalar_one_or_none.return_value = value
        results.append(result)
    session.execute = AsyncMock(side_effect=results)
    return session


@pytest.mark.asyncio
class TestRegionalRelevanceFilter:
    async def test_passes_when_no_context(self):
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Сегодня в Малмыже праздник")
        result = await flt.apply(post, context={})
        assert result.passed is True

    async def test_passes_when_post_has_no_text(self):
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="")
        session = _build_session()
        ctx = {"session": session, "region": _build_region()}
        result = await flt.apply(post, ctx)
        assert result.passed is True
        session.execute.assert_not_called()

    async def test_accepts_post_matching_region_words(self):
        region = _build_region()
        cfg = RegionConfig(
            region_code="mi",
            region_words={"kirov": ["Малмыжский"]},
        )
        session = _build_session(cfg)  # первый запрос — RegionConfig
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="В Малмыже прошёл фестиваль")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is True
        assert "Малмыжский" in result.metadata["regional_matches"]
        assert result.score_modifier > 0

    async def test_rejects_post_without_region_keywords(self):
        region = _build_region()
        cfg = RegionConfig(
            region_code="mi",
            region_words={"kirov": ["Малмыжский"]},
        )
        session = _build_session(cfg)
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Совсем неважный текст про автомобили")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is False
        assert "Not regionally relevant" in (result.reason or "")

    async def test_falls_back_to_region_name_when_no_config(self):
        """Без RegionConfig фильтр должен использовать имя/код региона."""
        region = _build_region(code="kukmor", name="КУКМОР - ИНФО")
        # RegionConfig отсутствует
        session = _build_session(None)
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="В Кукморе открыли новый стадион")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is True

    async def test_localities_extend_keywords(self):
        region = _build_region()
        cfg = RegionConfig(
            region_code="mi",
            region_words={},
            localities=["Цепочкино", "Калинино"],
        )
        session = _build_session(cfg)
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Жители Цепочкино отметили день села")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is True

    async def test_keywords_cache_avoids_second_db_call(self):
        region = _build_region()
        cfg = RegionConfig(region_code="mi", region_words={"k": ["Малмыжский"]})
        session = _build_session(cfg)  # БД отвечает только один раз
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Малмыж жив")
        await flt.apply(post, {"session": session, "region": region})
        # Второй вызов — кеш должен сработать
        post2 = SimpleNamespace(text="Малмыж снова в новостях")
        await flt.apply(post2, {"session": session, "region": region})
        assert session.execute.call_count == 1

    async def test_resolves_region_by_id_only(self):
        """Если в context есть region_id (но не region) — фильтр догружает Region."""
        region = _build_region()
        cfg = RegionConfig(region_code="mi", region_words={"k": ["Малмыж"]})
        # Сначала вернётся Region (по id), потом RegionConfig (по code)
        session = _build_session(region, cfg)
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Малмыжский фестиваль")
        result = await flt.apply(post, {"session": session, "region_id": region.id})
        assert result.passed is True
        assert session.execute.call_count == 2

    async def test_required_matches_threshold(self):
        region = _build_region()
        cfg = RegionConfig(region_code="mi", region_words={"k": ["Малмыж", "Уржум"]})
        session = _build_session(cfg)
        flt = RegionalRelevanceFilter(required_matches=2)
        # Только одно совпадение — не проходит
        post = SimpleNamespace(text="Малмыж и его жители")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is False

    async def test_score_modifier_capped(self):
        region = _build_region()
        cfg = RegionConfig(region_code="mi", region_words={"k": ["Малмыж", "Кукмор", "Уржум", "Лебяжье"]})
        session = _build_session(cfg)
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Малмыж Кукмор Уржум Лебяжье — все районы Кировской области")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is True
        assert result.score_modifier <= 20  # cap from filter code

    async def test_generic_name_tokens_filtered_out(self):
        """Слова вроде 'ИНФО' из названия региона не должны порождать совпадение.

        Берём регион «МАЛМЫЖ - ИНФО» и текст, где встречается только
        слово «информация»: оно не должно зацепить токен «ИНФО».
        """
        region = _build_region()
        session = _build_session(None)  # RegionConfig отсутствует
        flt = RegionalRelevanceFilter()
        post = SimpleNamespace(text="Сегодня важная информация для жителей")
        result = await flt.apply(post, {"session": session, "region": region})
        assert result.passed is False
        assert result.metadata["matches"] == []

    async def test_invalidate_cache(self):
        flt = RegionalRelevanceFilter()
        flt._keywords_cache[1] = ({"x"}, time.monotonic())
        flt._keywords_cache[2] = ({"y"}, time.monotonic())
        flt.invalidate_cache(region_id=1)
        assert 1 not in flt._keywords_cache
        assert 2 in flt._keywords_cache
        flt.invalidate_cache()
        assert flt._keywords_cache == {}
