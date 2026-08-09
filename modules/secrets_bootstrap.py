"""Секреты из vault КАРМАНа: аварийное восстановление + комната как источник.

Копируемая спека: ``brain_matrica/docs/specs/vault-client.md``. Эталон-образец —
trener; здесь — адаптация под SETKA.

**Два режима, и это решение владельца 2026-08-09.**

1. **Восстановление (сценарий A спеки).** Если ``/etc/setka/setka.env`` потерян
   (снесло файл, диск, рука), systemd стартует процесс без обязательных
   переменных — и ``config/runtime._require("DATABASE_URL")`` убьёт приложение
   ещё на импорте. Модуль вызывается ДО импорта ``config.runtime`` и тянет
   недостающее из комнаты ``setka``.

2. **Комната как источник (``VAULT_PULL_ALWAYS``, включено по умолчанию).**
   Владелец: «для этого мы комнату ключей и создавали, чтобы не лазить на
   продсерверы с ключами, а в КАРМАНе их хранить». Значит новый секрет должен
   доезжать до процесса, будучи положенным ТОЛЬКО в комнату — без правки
   ``setka.env`` руками по ssh.

   Изначально модуль ходил в vault лишь при аварии (``local-env-intact`` —
   ранний выход), и секрет, лежащий в комнате, до приложения не доезжал никогда.
   Обнаружено 2026-08-09 на ``DEEPSEEK_API_KEY``: ключ лежал в комнате, а
   ``os.environ`` его не видел.

   Цена режима — один сетевой вызов на старт процесса. Он best-effort и с
   таймаутом: недоступный vault даёт WARNING и продолжение, а не отказ старта.

Свойства 2, 3, 6, 7 ниже действуют в ОБОИХ режимах — «источник» не значит
«доверяем всему, что пришло»: allowlist, приоритет локального значения и запрет
на bootstrap-конфиг остаются ровно теми же.

Обязательные свойства спеки (см. ``docs/specs/vault-client.md``):

1. Сетевой вызов — один на старт (или ноль, если ``VAULT_PULL_ALWAYS=0`` и
   локальный env цел).
2. **Allowlist, а не «всё, что пришло».** Принимаем только имена из
   ``ACCEPTED_NAMES`` и префиксов ``ACCEPTED_PREFIXES``. ``NODE_OPTIONS`` /
   ``LD_PRELOAD`` / любой чужой ключ в комнату не проедет (мутационная приёмка
   поняла это дословно на trener). **Якорь:** новый секрет в ``.env.example`` /
   ``/etc/setka/setka.env`` → добавить и сюда.
3. Не перетирать то, что уже дал systemd: локальное значение сильнее.
4. Best-effort: токена нет или vault недоступен → WARNING в лог, продолжаем.
5. Bootstrap-токен живёт ОТДЕЛЬНО от восстанавливаемого файла — системный
   unit грузит ``SECRETS_TOKEN`` из отдельного ``secrets-token.env``
   (права 600), не из ``setka.env``.
6. **Bootstrap-конфиг вне allowlist принципиально**: ``SECRETS_TOKEN`` и
   ``SECRETS_VAULT_URL`` в ``ACCEPTED`` НИКОГДА не попадают — иначе комната
   может перенаправить все обращения клиента на чужой URL.
7. Проигнорированные ключи логируются ПО ИМЕНАМ, без значений: чужой ключ в
   комнате — сигнал инцидента, а не шум.
"""

from __future__ import annotations

import logging
import os
import urllib.request
from typing import Any, Dict, List, Optional

# Хост vault КАРМАНа из спеки (адресован как SECRETS_VAULT_URL env, см. свойство 6).
VAULT_URL = "https://831d0ce99bdf.vps.myjino.ru/api/secrets"
# Секреты, без которых SETKA не стартует (ровно те, что config.runtime требует
# через _require). Их отсутствие = «локальная копия потеряна».
REQUIRED_NAMES: tuple = ("DATABASE_URL", "REDIS_URL")

# ⚠️ Allowlist, а не «всё, что пришло» (свойство 2 спеки). Только имена отсюда —
# плюс префиксы ниже. Всё остальное из ответа игнорируется, даже если пришло
# (мутационная приёмка: RCE-сценарий через NODE_OPTIONS воспроизводился на
# образце trener дословно).
# Якорь: новый рантайм-секрет в .env.example / /etc/setka/setka.env → добавить
# и сюда (пара меняется вместе). НИКОГДА: SECRETS_TOKEN / SECRETS_VAULT_URL.
ACCEPTED_NAMES: frozenset = frozenset(
    {
        # обязательные для старта
        "DATABASE_URL",
        "REDIS_URL",
        # подпись сессий (без неё все сессии разваливаются на рестарте)
        "SETKA_WEB_SECRET",
        # VK
        "VK_API_VERSION",
        "VK_NEVER_PUBLISH_TOKEN_NAMES",
        "VK_PUBLISH_TOKEN_NAME",
        "VK_PUBLISH_TOKEN_NAMES",
        "VK_RESERVE_PUBLISH_TOKEN_NAMES",
        "VK_TEST_GROUP_ID",
        "VK_PRODUCTION_GROUPS",
        # AI
        "GROQ_API_KEY",
        # DeepSeek — движок классификации контент-конвейера (решение владельца
        # 2026-08-09). Ключ живёт в комнате КАРМАНа и приезжает сюда bootstrap'ом;
        # на прод-бокс руками он не кладётся — ровно для этого комната и заводилась.
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_MODEL",
        # контент-конвейер ВК → сайты (D-015)
        "CONVEYOR_DISABLED",
        "CONVEYOR_SITES",
        "CONVEYOR_SOURCE_DAYS",
        "CONVEYOR_BATCH_MAX",
        # Telegram (уведомления)
        "TELEGRAM_ALERT_CHAT_ID",
        # экосистема / шлюз / self-serve
        "ECOSYSTEM_KEY",
        "GATEWAY_QUOTA_PER_MIN",
        "GATEWAY_QUOTA_PER_DAY",
        "GATEWAY_GLOBAL_QUOTA_PER_MIN",
        "GATEWAY_REQUESTS_RETENTION_DAYS",
        # классификатор (HITL)
        "CLASSIFIER_INGEST_KEY",
        "CLASSIFIER_PENDING_MAX",
        "CLASSIFIER_REGION_CODES",
        "CLASSIFIER_ENFORCE_ENABLED",
        # Радар / ЕСА
        "RADAR_INVITE_CODE",
        "RADAR_VAPID_PRIVATE_KEY",
        "RADAR_VAPID_SUBJECT",
        "RADAR_ID_VK_APP_ID",
        "RADAR_ID_CLIENT_ID",
        "RADAR_BOT_NAME",
        "RADAR_BOT_ALLOWED_USERS",
        "RADAR_VK_COMMUNITY_ID",
        "SESSION_COOKIE_DOMAIN",
        # доменные хвосты из setka.env
        "TG_RELAY_SECRET",
        "TG_PREVIEW_RELAY_URL",
        "CLOUDFLARE_API_TOKEN",
        "AD_AUTO_GREETING_COMMUNITIES",
        "AD_AUTO_GREETING_TEXT",
        "BULLETIN_CURATION_SHADOW_ENABLED",
        "BULLETIN_CURATION_REGION_CODES",
        "COLLECTION_AUDIT_SHADOW_ENABLED",
        "COLLECTION_AUDIT_REGION_CODES",
        "KRUGOZOR_BROADCAST_DISABLED",
        "KRUGOZOR_TARGET_REGION_CODES",
        "COPY_SETKA_DISABLED",
        "BROADCAST_DISABLED",
        "RADAR_DELIVERY_DISABLED",
        "PRODUCTION_WORKFLOW_CONFIG",
        # пул соединений (не секреты, но часть восстанавливаемого env)
        "DB_POOL_SIZE",
        "DB_MAX_OVERFLOW",
        "SQLALCHEMY_ECHO",
    }
)
# Множественные токены приходят префиксами (как _collect_prefixed_tokens):
# VK_TOKEN_<NAME> и TELEGRAM_TOKEN_<NAME>. Их приём — тоже явное правило, но
# имя переменной неизвестно заранее (пишется владельцем). Разрешаем префикс,
# а не «всё»: NODE_OPTIONS под этот префикс не подходит.
ACCEPTED_PREFIXES: tuple = ("VK_TOKEN_", "TELEGRAM_TOKEN_")
# Ключи доставки контент-конвейера на сайты кластера приходят суффиксом:
# <SITE>_INGEST_KEY (имя задал КАРМАН при переносе, см. директиву 2026-07-26).
# Суффикс, а не префикс, потому что имя уже согласовано тремя сторонами и менять
# его дороже, чем добавить правило. Правило узкое: под него не подходит ни один
# bootstrap-секрет и ни один посторонний ключ вроде NODE_OPTIONS.
#
# Направление ключа читается из имени, и это не косметика: GATEWAY_KEY_<PROJECT>
# в SETKA означает ВХОДЯЩИЙ ключ VK-шлюза, и исходящий ключ доставки под тем же
# именем при недоступной БД открывал бы наш шлюз (modules/gateway/keys.py).
ACCEPTED_SUFFIXES: tuple = ("_INGEST_KEY",)
# Bootstrap-токен и URL хранилища — вне любых правил приёма (свойство 6).
_BOOTSTRAP_NAMES_FORBIDDEN = frozenset(
    {"SECRETS_TOKEN", "SECRETS_VAULT_URL", "SECRETS_MANAGER_URL"}
)

log = logging.getLogger(__name__)


def _missing_required(env: Dict[str, str]) -> List[str]:
    """Имена REQUIRED, которых нет в окружении (в любом порядке). Пусто = цел."""
    return [k for k in REQUIRED_NAMES if not env.get(k)]


def _pull_always(env: Dict[str, str]) -> bool:
    """Тянуть комнату на каждом старте, а не только при потере env?

    Включено по умолчанию (решение владельца 2026-08-09): комната — источник
    ключей, иначе секрет, положенный только туда, до процесса не доезжает.
    Выключатель ``VAULT_PULL_ALWAYS=0`` оставлен на случай, когда сетевой вызов
    на старте мешает — например при отладке без доступа к vault.
    """
    raw = (env.get("VAULT_PULL_ALWAYS") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _accepted(name: str) -> bool:
    """Имя принимается allowlist'ом? (свойства 2 и 6 спеки).

    Явные имена + разрешённые префиксы + суффикс ключей доставки. Bootstrap-имена
    (SECRETS_TOKEN и „братья“) не пройдут ни одним из путей: они не начинаются с
    VK_TOKEN_ / TELEGRAM_TOKEN_ и не кончаются на _INGEST_KEY — двойная страховка
    плюс явная проверка ниже жёстко запрещает их в любом виде.
    """
    if name in _BOOTSTRAP_NAMES_FORBIDDEN:
        return False
    if name in ACCEPTED_NAMES:
        return True
    if any(name.startswith(p) for p in ACCEPTED_PREFIXES):
        return True
    return any(name.endswith(s) for s in ACCEPTED_SUFFIXES)


def _fetch_secrets(token: str, vault_url: str) -> Dict[str, str]:
    """GET /api/secrets из комнаты setka. ``{name: value}`` либо raise."""
    req = urllib.request.Request(
        vault_url,
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:  # vault не вешает старт
        payload: Any = __import__("json").load(resp)
    secrets = payload.get("secrets") or {}
    if not isinstance(secrets, dict):
        raise RuntimeError("vault response: secrets is not a dict")
    return {str(k): str(v) for k, v in secrets.items() if str(v)}


def bootstrap_secrets(
    env: Optional[Dict[str, str]] = None,
    vault_url: Optional[str] = None,
    token: Optional[str] = None,
) -> Dict[str, Any]:
    """Восстановить REQUIRED-секреты из vault, если локальная копия потеряна.

    Рантайм-вызов: ``bootstrap_secrets()`` читает ``os.environ``. Параметры
    ``env``/``vault_url``/``token`` — только для тестов (чистая функция).

    Возвращает ``{"recovered": int, "ignored": [names], "reason": str}``.
    Весь модуль никогда не валит старт: худший исход — WARNING и continue
    (свойство 4), приложение упадёт дальше по своей настоящей причине.
    """
    env = env if env is not None else os.environ

    missing = _missing_required(env)
    pull_always = _pull_always(env)
    if not missing and not pull_always:
        return {"recovered": 0, "ignored": [], "reason": "local-env-intact"}

    token = token if token is not None else env.get("SECRETS_TOKEN")
    if not token:
        if not missing:
            # Локальный env цел — это не авария, а всего лишь несделанный pull.
            # Отличаем от настоящей потери файла: там WARNING обязателен.
            return {"recovered": 0, "ignored": [], "reason": "no-token"}
        log.warning(
            "SETKA: локальная копия env потеряна (%s), SECRETS_TOKEN не задан — "
            "восстановление из vault невозможно",
            ", ".join(missing),
        )
        return {"recovered": 0, "ignored": [], "reason": "no-token"}

    url = vault_url if vault_url is not None else (env.get("SECRETS_VAULT_URL") or VAULT_URL)
    try:
        fetched = _fetch_secrets(token, url)
    except Exception as e:  # noqa: BLE001 — best-effort, см. свойство 4
        log.error("SETKA: восстановление из vault не удалось: %s", e)
        return {"recovered": 0, "ignored": [], "reason": "fetch-failed"}

    recovered, ignored = 0, []
    for name, value in fetched.items():
        if not _accepted(name):
            ignored.append(name)  # по именам, не по значениям (свойство 7)
            continue
        if env.get(name) is not None:
            continue  # systemd сильнее (свойство 3)
        env[name] = value
        recovered += 1

    if ignored:
        # имена, НЕ значения (свойство 7): чужой ключ в комнате — сигнал инцидента
        log.warning("SETKA: вне allowlist, проигнорированы: %s", ", ".join(sorted(ignored)))
    if recovered or missing:
        # WARNING, не INFO: bootstrap-хук выполняется ДО logging.basicConfig в main.py,
        # у root-логгера ещё нет handlers, и INFO отбросится. Восстановление по спеке —
        # всё равно сигнал инцидента (локальная копия была потеряна), он обязан дойти
        # до лога даже через lastResort-хендлер.
        #
        # Молчим, когда pull ничего не добавил и авария не случалась: в режиме
        # «комната как источник» этот путь проходится на КАЖДОМ старте, и строка
        # «восстановлено: 0» превратилась бы в фоновый шум, на котором перестанут
        # замечать настоящее восстановление.
        log.warning("SETKA: восстановлено секретов из vault: %d", recovered)
    return {
        "recovered": recovered,
        "ignored": ignored,
        "reason": "recovered" if missing else "pulled",
    }
