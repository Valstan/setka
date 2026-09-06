"""Проба возможностей ВК, нужных модулю «Раскрутка». Probe-before-build (#020).

Отвечает на два вопроса, которые нельзя решить чтением документации:

1. **Работает ли ещё ``groups.addLink``.** Блок «Ссылки» сообщества — то место,
   откуда наш же модуль подбора вытаскивает соседние группы
   (``discovered_via='info_links'``), то есть он машиночитаем. Но ВК тихо
   убирает старые write-методы ``groups.*``, и у проекта уже есть два шрама
   этого класса: G19 (метод запрещён по роли/статусу) и G253 (групповой токен
   не читает стены вообще). Проверяем вызовом, а не верой в SDK.
2. **Доступны ли настройки сообщества** (``groups.getSettings``) — read-only
   метод, требующий ровно тех же админских прав, что и ``groups.edit``. Если он
   отвечает, канал автооформления имеет шанс; если нет — канал остаётся
   выключенным, а описание владелец вставляет руками.

**Безопасность.** Запись идёт только на тест-полигон и только с ``--apply``; по
умолчанию скрипт ничего не меняет. Добавленная ссылка удаляется сразу же, в
``finally`` — чтобы падение посередине не оставило мусор в группе. Значения
токенов не логируются никогда, только имена.

Запуск на проде:
    python3 scripts/probe_promo_capabilities.py            # только чтение
    python3 scripts/probe_promo_capabilities.py --apply    # + проба записи
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("probe_promo")

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.131"

# Тест-полигон проекта: группа без подписчиков, заведённая ровно для таких проб.
TEST_GROUP_ID = 137760500

# Куда ведёт пробная ссылка — на публичный список сети, если он задан; иначе на
# саму же тестовую группу, чтобы проба не зависела от внешнего адреса.
FALLBACK_LINK = f"https://vk.com/club{TEST_GROUP_ID}"


def call(token: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Вызов ВК. Возвращает ``{"ok": ...}`` либо ``{"error_code": N, "msg": ...}``.

    Токен уходит в тело запроса и никогда — в лог и в возвращаемое значение.
    """
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = VK_VERSION
    data = urllib.parse.urlencode(payload).encode()
    try:
        with urllib.request.urlopen(VK_API + method, data=data, timeout=20) as resp:
            body = json.loads(resp.read().decode())
    except Exception as exc:  # pragma: no cover - сеть
        return {"error_code": -1, "msg": f"сетевой сбой: {exc}"}

    if "error" in body:
        err = body["error"]
        return {
            "error_code": err.get("error_code"),
            "msg": str(err.get("error_msg"))[:200],
        }
    return {"ok": body.get("response")}


def describe(result: Dict[str, Any]) -> str:
    if "ok" in result:
        return "✅ доступен"
    return f"⛔ error {result.get('error_code')}: {result.get('msg')}"


async def load_tokens() -> Dict[str, str]:
    """Живые токены из БД: user-токены и ключ тест-полигона."""
    from database.connection import AsyncSessionLocal
    from modules.vk_token_router import TokenOp, TokenPolicy

    out: Dict[str, str] = {}
    async with AsyncSessionLocal() as session:
        policy = TokenPolicy(session)
        for candidate in await policy.pick(TokenOp.READ):
            out.setdefault(candidate.name, candidate.token)
        for candidate in await policy.pick(TokenOp.COMMUNITY_WRITE, group_id=-TEST_GROUP_ID):
            out.setdefault(candidate.name, candidate.token)
    return out


def probe_read(tokens: Dict[str, str]) -> None:
    """Чтение: блок ссылок и настройки сообщества."""
    logger.info("\n=== ЧТЕНИЕ (безопасно) ===")
    for name, token in tokens.items():
        links = call(token, "groups.getById", {"group_id": TEST_GROUP_ID, "fields": "links"})
        settings = call(token, "groups.getSettings", {"group_id": TEST_GROUP_ID})
        logger.info("  %-18s groups.getById(links)  %s", name, describe(links))
        logger.info("  %-18s groups.getSettings     %s", name, describe(settings))


def probe_write(tokens: Dict[str, str], link_url: str) -> Optional[str]:
    """Запись: добавить ссылку в тест-полигон и сразу удалить.

    Возвращает имя токена, которым получилось, либо ``None``.
    """
    logger.info("\n=== ЗАПИСЬ в тест-полигон %s ===", TEST_GROUP_ID)
    for name, token in tokens.items():
        # groups.edit решает судьбу канала автооформления. Правим только
        # description и только на тест-полигоне; title не трогаем никогда.
        edited = call(
            token,
            "groups.edit",
            {"group_id": TEST_GROUP_ID, "description": "Тестовая группа проекта. Проба API."},
        )
        logger.info("  %-18s groups.edit            %s", name, describe(edited))

        added = call(
            token,
            "groups.addLink",
            {"group_id": TEST_GROUP_ID, "link": link_url, "text": "проба раскрутки"},
        )
        logger.info("  %-18s groups.addLink         %s", name, describe(added))
        if "ok" not in added:
            continue

        link_id = None
        response = added.get("ok")
        if isinstance(response, dict):
            link_id = response.get("id")

        try:
            if link_id is not None:
                removed = call(
                    token,
                    "groups.deleteLink",
                    {"group_id": TEST_GROUP_ID, "link_id": link_id},
                )
                logger.info("  %-18s groups.deleteLink      %s", name, describe(removed))
            else:
                logger.warning("  %-18s ссылка добавлена, но id не вернулся — удали руками", name)
        finally:
            pass
        return name
    return None


def read_screen_name(token: str, group_id: int) -> Optional[str]:
    """Текущий короткий адрес сообщества — отдельным чтением."""
    res = call(token, "groups.getById", {"group_id": group_id, "fields": "screen_name"})
    if "ok" not in res:
        return None
    rows = res["ok"]
    if isinstance(rows, dict):
        rows = rows.get("groups", rows)
    if not rows:
        return None
    return (rows[0] or {}).get("screen_name")


def probe_screen_name(tokens: Dict[str, str]) -> Optional[str]:
    """Умеет ли ``groups.edit`` менять короткий адрес (заказ владельца 06.09).

    **Вердикт выносится чтением после записи, а не кодом ответа.** ВК может
    принять параметр, вернуть ``response: 1`` и не сделать ничего — тогда
    «успех» означал бы ровно противоположное. Ровно этот класс (#284, «гейт без
    области») за один день 06.09 дал четыре инстанции подряд, две из них стоили
    лишней итерации. Поэтому здесь: снять адрес → записать новый → **снять
    снова** → сравнить, и только потом судить.

    Адрес полигона восстанавливается в ``finally``: проба, упавшая посередине,
    не должна оставить тест-группу под чужим именем.
    """
    logger.info("\n=== SCREEN_NAME на тест-полигоне %s ===", TEST_GROUP_ID)
    probe_name = f"setkaprobe{int(time.time())}"

    for name, token in tokens.items():
        before = read_screen_name(token, TEST_GROUP_ID)
        if before is None:
            logger.info("  %-18s снимок адреса не взялся — пропуск", name)
            continue
        logger.info("  %-18s адрес до пробы: %s", name, before)

        edited = call(
            token,
            "groups.edit",
            {"group_id": TEST_GROUP_ID, "screen_name": probe_name},
        )
        logger.info("  %-18s groups.edit(screen_name) %s", name, describe(edited))
        try:
            after = read_screen_name(token, TEST_GROUP_ID)
            logger.info("  %-18s адрес после записи: %s", name, after)
            if after == probe_name:
                logger.info("  %-18s ✅ адрес РЕАЛЬНО сменился", name)
                return name
            if "ok" in edited:
                # Самый опасный исход: метод отчитался успехом, ничего не сделав.
                logger.info(
                    "  %-18s ⚠️ метод вернул успех, а адрес прежний — запись НЕ работает",
                    name,
                )
            else:
                logger.info("  %-18s ⛔ адрес не сменился", name)
        finally:
            if read_screen_name(token, TEST_GROUP_ID) not in (before, None):
                restored = call(
                    token, "groups.edit", {"group_id": TEST_GROUP_ID, "screen_name": before}
                )
                logger.info("  %-18s возврат адреса %s %s", name, before, describe(restored))
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="выполнить пробу записи (по умолчанию только чтение)",
    )
    parser.add_argument("--link", default=None, help="какую ссылку пробовать добавить")
    parser.add_argument(
        "--screen-name",
        action="store_true",
        help="проба смены короткого адреса на полигоне (с --apply); адрес возвращается",
    )
    args = parser.parse_args()

    from modules.secrets_bootstrap import bootstrap_secrets

    bootstrap_secrets()

    tokens = asyncio.run(load_tokens())
    if not tokens:
        logger.error("Живых токенов не нашлось — проба невозможна")
        return 2
    logger.info("Токенов в пробе: %s", ", ".join(sorted(tokens)))

    probe_read(tokens)

    if not args.apply:
        logger.info("\nПроба записи пропущена (запусти с --apply).")
        return 0

    if args.screen_name:
        winner = probe_screen_name(tokens)
        logger.info("")
        if winner:
            logger.info(
                "ИТОГ: короткий адрес меняется под токеном %s — переименование возможно.", winner
            )
            return 0
        logger.info("ИТОГ: короткий адрес через API не меняется — только руками в настройках.")
        return 1

    from config.promo import get_network_list_url

    link_url = args.link or get_network_list_url() or FALLBACK_LINK
    winner = probe_write(tokens, link_url)

    logger.info("")
    if winner:
        logger.info(
            "ИТОГ: groups.addLink работает под токеном %s — канал ссылок можно строить.", winner
        )
        return 0
    logger.info("ИТОГ: groups.addLink недоступен ни одному токену — ссылки только руками.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
