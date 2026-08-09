"""Tests for modules.secrets_bootstrap — vault recovery client (scenario A).

Покрывают обязательные свойства спеки vault-client.md: no-op в норме (ноль
сетевых вызовов), allowlist вместо «всё, что пришло» (мутационная приёмка:
NODE_OPTIONS/LD_PRELOAD не пролезают), bootstrap-конфиг вне allowlist, локальное
значение сильнее, best-effort (нет токена / vault недоступен → WARNING, не падаем),
логирование неучтённых ключей только по именам.
"""

from __future__ import annotations

import logging

import pytest

from modules import secrets_bootstrap as sb


def test_intact_local_env_is_noop_without_network(monkeypatch):
    """Свойство 1: всё на месте → ноль сетевых вызовов, recovered 0."""
    calls = []

    def _fake_fetch(*a, **k):  # pragma: no cover — не должен вызываться
        calls.append(a)
        return {}

    env = {"DATABASE_URL": "postgresql+asyncpg://u:p@h/db", "REDIS_URL": "redis://h:1/0"}
    monkeypatch.setattr(sb, "_fetch_secrets", _fake_fetch)

    res = sb.bootstrap_secrets(env=env)
    assert res["reason"] == "local-env-intact"
    assert res["recovered"] == 0
    assert calls == []


def test_recovers_missing_required_from_vault(monkeypatch):
    """Сценарий A: нет DATABASE_URL/REDIS_URL → берём из vault."""
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "SETKA_WEB_SECRET": "svc",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert res["reason"] == "recovered"
    assert res["recovered"] == 3
    assert env["DATABASE_URL"].startswith("postgresql+asyncpg")
    assert env["REDIS_URL"].startswith("redis://")
    assert env["SETKA_WEB_SECRET"] == "svc"


def test_foreign_keys_ignored_by_allowlist(monkeypatch):
    """Свойство 2 (мутационная приёмка): чужой ключ из vault не проезжает.

    RCE-сценарий образца trener — NODE_OPTIONS / LD_PRELOAD: даже если они
    лежат в комнате, клиент их НЕ пишет в окружение.
    """
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "NODE_OPTIONS": "--require /proc/self/environ",
            "LD_PRELOAD": "/tmp/evil.so",
            "PYTHONPATH": "/tmp/evil",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert res["recovered"] == 2  # только DATABASE_URL + REDIS_URL
    assert sorted(res["ignored"]) == ["LD_PRELOAD", "NODE_OPTIONS", "PYTHONPATH"]
    assert "NODE_OPTIONS" not in env
    assert "LD_PRELOAD" not in env
    assert "PYTHONPATH" not in env


def test_bootstrap_config_never_accepted_from_vault(monkeypatch):
    """Свойство 6: SECRETS_TOKEN / SECRETS_VAULT_URL принципиально вне allowlist."""
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "SECRETS_TOKEN": "stolen-token",
            "SECRETS_VAULT_URL": "https://evil.example/api/secrets",
            "SECRETS_MANAGER_URL": "https://evil.example/manage",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert res["recovered"] == 2
    assert sorted(res["ignored"]) == ["SECRETS_MANAGER_URL", "SECRETS_TOKEN", "SECRETS_VAULT_URL"]
    assert env["SECRETS_TOKEN"] == "tok"  # оригинал не перетёрся
    assert env.get("SECRETS_VAULT_URL") is None


def test_local_value_wins_over_vault(monkeypatch):
    """Свойство 3: systemd дал ключ → vault не перетирает локальное."""
    env = {
        "SECRETS_TOKEN": "tok",
        "DATABASE_URL": "postgresql+asyncpg://local:p@h/db",
        # REDIS_URL отсутствует — его надо восстановить
    }
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://vault:p@h/db",
            "REDIS_URL": "redis://h:1/0",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert res["recovered"] == 1  # только REDIS_URL
    assert env["DATABASE_URL"] == "postgresql+asyncpg://local:p@h/db"


def test_no_token_returns_no_token(monkeypatch):
    """Свойство 4: нет SECRETS_TOKEN → best-effort no-op (не падаем)."""
    env = {}  # и REQUIRED нет, и токена нет
    monkeypatch.setattr(sb, "_fetch_secrets", lambda *a, **k: pytest.fail("не должен вызываться"))

    res = sb.bootstrap_secrets(env=env)
    assert res["reason"] == "no-token"
    assert res["recovered"] == 0


def test_vault_unreachable_is_best_effort(monkeypatch):
    """Свойство 4: vault недоступен → fetch-failed, старт НЕ валим."""
    env = {"SECRETS_TOKEN": "tok"}

    def _boom(*a, **k):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(sb, "_fetch_secrets", _boom)
    res = sb.bootstrap_secrets(env=env)
    assert res["reason"] == "fetch-failed"
    assert res["recovered"] == 0


def test_prefixes_accepted():
    """Множественные токены (VK_TOKEN_* / TELEGRAM_TOKEN_*) проходят по префиксу."""
    assert sb._accepted("VK_TOKEN_VALSTAN")
    assert sb._accepted("VK_TOKEN_MAMA")
    assert sb._accepted("TELEGRAM_TOKEN_AFONYA")
    assert sb._accepted("DATABASE_URL")
    assert not sb._accepted("FOO_BAR")
    assert not sb._accepted("NODE_OPTIONS")
    assert not sb._accepted("SECRETS_TOKEN")


def test_ignored_logged_by_names_only(monkeypatch, caplog):
    """Свойство 7: чужие ключи логируются ПО ИМЕНАМ, без значений."""
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "MYSTERY": "super-secret-value-12345",
        },
    )

    with caplog.at_level(logging.WARNING, logger="modules.secrets_bootstrap"):
        sb.bootstrap_secrets(env=env)

    assert "MYSTERY" in caplog.text
    assert "super-secret-value-12345" not in caplog.text


def test_recovered_logs_count(monkeypatch, caplog):
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
        },
    )

    # WARNING (не INFO) — хук выполняется до logging.basicConfig, должен пройти
    # даже через lastResort-хендлер root-логгера.
    with caplog.at_level(logging.WARNING, logger="modules.secrets_bootstrap"):
        sb.bootstrap_secrets(env=env)

    assert "восстановлено секретов из vault: 2" in caplog.text


def test_site_ingest_keys_accepted_by_suffix(monkeypatch):
    """Ключи доставки конвейера приходят как <SITE>_INGEST_KEY (имя от КАРМАНа).

    Суффиксное правило узкое: пускает ключи сайтов и не пускает ни bootstrap-имена,
    ни посторонние вроде NODE_OPTIONS, лежащего в той же комнате.
    """
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "VMALMYZHE_INGEST_KEY": "site-key-1",
            "CDK_KALININO_INGEST_KEY": "site-key-2",
            "NODE_OPTIONS": "--require /proc/self/environ",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert env["VMALMYZHE_INGEST_KEY"] == "site-key-1"
    assert env["CDK_KALININO_INGEST_KEY"] == "site-key-2"
    assert res["ignored"] == ["NODE_OPTIONS"]


def test_gateway_key_name_is_not_smuggled_in_by_suffix_rule(monkeypatch):
    """GATEWAY_KEY_* — ключи ВХОДЯЩИХ в наш VK-шлюз, из vault они не принимаются.

    Проверяем, что суффиксное правило не открыло им дверь: имя, начинающееся с
    GATEWAY_KEY_, не оканчивается на _INGEST_KEY и остаётся за бортом.
    """
    env = {"SECRETS_TOKEN": "tok"}
    monkeypatch.setattr(
        sb,
        "_fetch_secrets",
        lambda token, url: {
            "DATABASE_URL": "postgresql+asyncpg://u:p@h/db",
            "REDIS_URL": "redis://h:1/0",
            "GATEWAY_KEY_VMALMYZHE": "wrong-direction",
        },
    )

    res = sb.bootstrap_secrets(env=env)
    assert res["ignored"] == ["GATEWAY_KEY_VMALMYZHE"]
    assert "GATEWAY_KEY_VMALMYZHE" not in env
