"""
Unit tests for web.api.filtration helpers (без FastAPI/DB).
"""

import pytest

from web.api.filtration import FiltrationPutBody, _normalize_localities


class TestNormalizeLocalities:
    def test_empty_returns_empty(self):
        assert _normalize_localities(None) == []
        assert _normalize_localities([]) == []

    def test_strips_whitespace(self):
        assert _normalize_localities(["  Цепочкино  "]) == ["Цепочкино"]

    def test_drops_empty_strings(self):
        assert _normalize_localities(["", "  ", "Гоньба"]) == ["Гоньба"]

    def test_dedupes_case_insensitive(self):
        # Сохраняется первая встретившаяся форма
        result = _normalize_localities(["Цепочкино", "цепочкино", "ЦЕПОЧКИНО"])
        assert result == ["Цепочкино"]

    def test_dedupes_yo_e(self):
        # «Лебяжье» и «Лебяжье» (без ё) — один и тот же населённый пункт
        result = _normalize_localities(["Лебяжье", "Лебяжье"])
        assert len(result) == 1

    def test_preserves_order(self):
        result = _normalize_localities(["Калинино", "Гоньба", "Цепочкино"])
        assert result == ["Калинино", "Гоньба", "Цепочкино"]

    def test_skips_non_strings(self):
        # На случай если фронт пришлёт мусор (число, None) внутри списка
        result = _normalize_localities(["Гоньба", None, 42, "Цепочкино"])  # type: ignore[list-item]
        assert result == ["Гоньба", "Цепочкино"]


class TestFiltrationPutBodySchema:
    def test_localities_field_optional(self):
        # Все поля optional — пустой body должен валидироваться
        body = FiltrationPutBody()
        assert body.localities is None

    def test_localities_accepts_list_of_strings(self):
        body = FiltrationPutBody(localities=["Цепочкино", "Гоньба"])
        assert body.localities == ["Цепочкино", "Гоньба"]

    def test_localities_rejects_non_list(self):
        # Pydantic должен забраковать строку вместо списка
        with pytest.raises(Exception):
            FiltrationPutBody(localities="not a list")  # type: ignore[arg-type]
