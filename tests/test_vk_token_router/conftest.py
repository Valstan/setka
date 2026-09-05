"""Швы роутера: снапшот способностей в тестах не ходит в Redis.

``pick()`` читает снапшот ``token_capabilities`` (Redis) — без заглушки каждый
тест ждал бы коннект. Тест, которому нужна матрица, патчит
``_capabilities_matrix_safe`` сам (Этап 3, 2026-09-05).
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _capabilities_offline(monkeypatch):
    import modules.vk_token_router as router

    monkeypatch.setattr(router, "_capabilities_matrix_safe", lambda: None)
    monkeypatch.setitem(router._capabilities_cache, "at", None)
    monkeypatch.setitem(router._capabilities_cache, "matrix", None)
    yield
    router._capabilities_cache["at"] = None
    router._capabilities_cache["matrix"] = None
