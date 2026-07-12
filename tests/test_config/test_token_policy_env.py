"""Tests for env-side of TokenPolicy: publish whitelist / reserve / deny-list.

Не требуют БД. Семантика с 2026-07-12 (решение владельца — каскад публикации
community → VALSTAN → VITA):
- ``get_never_publish_token_names`` по умолчанию ПУСТ (env-override остаётся).
- ``get_reserve_publish_token_names`` — резервные публикаторы (default VITA),
  пробуются строго последними.
- ``get_publish_token`` возвращает резерв только когда основных токенов нет.
- ``validate_publish_token`` пропускает резерв (порядок держит TokenPolicy).
"""

import importlib
import os
from unittest.mock import patch


def _reload_runtime():
    import config.runtime

    importlib.reload(config.runtime)
    return config.runtime


class TestPublishTokenNames:
    def test_csv_list(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN,OLGA",
                "VK_TOKEN_VALSTAN": "tok_v",
                "VK_TOKEN_OLGA": "tok_o",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_publish_token_names() == ["VALSTAN", "OLGA"]

    def test_legacy_single_used_when_csv_absent(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAME": "valstan",
                "VK_TOKEN_VALSTAN": "tok_v",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_publish_token_names() == ["VALSTAN"]

    def test_empty_when_nothing_set(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            # legacy default — VALSTAN, даже если в env нет
            assert rt.get_publish_token_names() == ["VALSTAN"]


class TestNeverPublishTokenNames:
    def test_empty_by_default(self):
        """С 2026-07-12 deny-list по умолчанию пуст — Vita стала резервом."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_never_publish_token_names() == set()

    def test_override_via_env(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_NEVER_PUBLISH_TOKEN_NAMES": "vita, olga",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_never_publish_token_names() == {"VITA", "OLGA"}

    def test_empty_env_is_empty(self):
        """Пустой env-var = пустой deny-list (default тоже пуст)."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_NEVER_PUBLISH_TOKEN_NAMES": "",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_never_publish_token_names() == set()


class TestReservePublishTokenNames:
    def test_vita_by_default(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_reserve_publish_token_names() == ["VITA"]

    def test_override_via_env(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_RESERVE_PUBLISH_TOKEN_NAMES": "olga, mama",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_reserve_publish_token_names() == ["OLGA", "MAMA"]


class TestGetPublishToken:
    def test_picks_valstan_even_if_vita_first_in_whitelist(self):
        """Резерв (Vita) депприоритизирован даже внутри явного whitelist'а."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VITA,VALSTAN",
                "VK_TOKEN_VITA": "tok_vita",
                "VK_TOKEN_VALSTAN": "tok_val",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_publish_token() == "tok_val"

    def test_vita_as_last_resort_when_only_vita_present(self):
        """Основных токенов нет — резерв (Vita) подхватывает публикацию."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
                "VK_TOKEN_VITA": "tok_vita",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_publish_token() == "tok_vita"

    def test_never_deny_beats_reserve(self):
        """Hard deny через env выключает даже резерв."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
                "VK_NEVER_PUBLISH_TOKEN_NAMES": "VITA",
                "VK_TOKEN_VITA": "tok_vita",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.get_publish_token() is None


class TestValidatePublishToken:
    def test_vita_accepted_as_reserve(self):
        """Vita — легитимный резервный публикатор (порядок держит TokenPolicy)."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
                "VK_TOKEN_VITA": "tok_vita",
                "VK_TOKEN_VALSTAN": "tok_val",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.validate_publish_token("tok_vita", token_name="VITA") is True
            assert rt.validate_publish_token("tok_val", token_name="VALSTAN") is True

    def test_vita_rejected_when_hard_denied(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN,VITA",
                "VK_NEVER_PUBLISH_TOKEN_NAMES": "VITA",
                "VK_TOKEN_VITA": "tok_vita",
                "VK_TOKEN_VALSTAN": "tok_val",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.validate_publish_token("tok_vita", token_name="VITA") is False

    def test_unknown_name_outside_allowed_rejected(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
                "VK_TOKEN_OLGA": "tok_olga",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.validate_publish_token("tok_olga", token_name="OLGA") is False

    def test_token_match_without_name(self):
        """Без token_name — сверка по содержимому: резерв ок, чужой — нет."""
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_TOKEN_VITA": "tok_vita",
                "VK_TOKEN_VALSTAN": "tok_val",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.validate_publish_token("tok_vita") is True  # reserve — допустима
            assert rt.validate_publish_token("tok_val") is True
            assert rt.validate_publish_token("tok_stranger") is False
