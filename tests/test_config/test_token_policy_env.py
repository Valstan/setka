"""Tests for env-side of TokenPolicy: publish whitelist / deny-list helpers.

Не требуют БД. Проверяем что:
- ``get_publish_token_names`` корректно парсит CSV и fallback на legacy single.
- ``get_never_publish_token_names`` блокирует Vita по умолчанию.
- ``validate_publish_token`` не пропускает Vita даже если в env.
- ``get_publish_token`` НЕ возвращает Vita даже если она единственная в env.
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
            assert rt.get_never_publish_token_names() == {"VITA"}

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

    def test_empty_env_keeps_default(self):
        """Пустой env-var интерпретируется как «не задано» — Vita всё ещё deny."""
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
            assert rt.get_never_publish_token_names() == {"VITA"}


class TestGetPublishToken:
    def test_picks_valstan_skipping_vita(self):
        """Vita не должна попасть в publish-token даже если она в whitelist."""
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

    def test_returns_none_if_only_vita_present(self):
        """Если в env только Vita — publish-token = None (Vita запрещена)."""
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
            assert rt.get_publish_token() is None


class TestValidatePublishToken:
    def test_vita_rejected_by_name(self):
        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "postgresql+asyncpg://x:x@localhost/x",
                "REDIS_URL": "redis://localhost:6379/0",
                "VK_PUBLISH_TOKEN_NAMES": "VALSTAN,VITA",
                "VK_TOKEN_VITA": "tok_vita",
                "VK_TOKEN_VALSTAN": "tok_val",
            },
            clear=True,
        ):
            rt = _reload_runtime()
            assert rt.validate_publish_token("tok_vita", token_name="VITA") is False

    def test_vita_rejected_by_token_match(self):
        """Даже если caller не передал token_name — Vita исключается по содержимому."""
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
            assert rt.validate_publish_token("tok_vita") is False
            assert rt.validate_publish_token("tok_val") is True
