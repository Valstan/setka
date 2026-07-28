"""Конфиг self-serve подключения к экосистеме (ADR-0010, мандат brain 2026-07-28).

Решение владельца: внутри экосистемы @valstan стандартные подключения делаются
**без человека в цикле** — проект-клиент сам получает клиента ЕСА (OIDC) и ключ
VK-шлюза, аутентифицируясь ОДНИМ экосистемным ключом. Раньше и то и другое
выдавалось руками по письму, и цена ручной процедуры платилась не тем, кто её
выбрал: ``GATEWAY_KEY_KAZANSKAYA`` провисел 16 дней просто потому, что выдача
требовала моего захода.

Секрет — только в env (``/etc/setka/setka.env``), как VK-токены и ключи шлюза;
зеркало кладётся в vault-комнату КАРМАНа и раздаётся grant'ами проектам. Через
Мозг значение не ходит.

Env vars:
  ECOSYSTEM_KEY=...                  # общий ключ выдачи; не задан → 503
  ECOSYSTEM_PROVISIONING_DISABLED=0  # аварийный kill-switch (1/true/yes/on)
"""

from __future__ import annotations

import os
from typing import Optional


def get_ecosystem_key() -> Optional[str]:
    """Экосистемный ключ выдачи (env ``ECOSYSTEM_KEY``); ``None`` — не настроен.

    Читается при каждом вызове (ротация ключа не требует рестарта модуля —
    та же семантика, что у ``GATEWAY_KEY_*`` и ``VK_TOKEN_*``).
    """
    value = (os.getenv("ECOSYSTEM_KEY") or "").strip()
    return value or None


def provisioning_disabled() -> bool:
    """Kill-switch self-serve выдачи (env ``ECOSYSTEM_PROVISIONING_DISABLED``).

    Дефолт — включено. Нужен на случай утечки экосистемного ключа: выдачу
    гасим одной переменной, не трогая уже выданных клиентов и ключей.
    """
    return os.getenv("ECOSYSTEM_PROVISIONING_DISABLED", "0").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
