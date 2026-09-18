"""Кто из сети принимает предложенные новости — проба глазами ПОСТОРОННЕГО.

Зачем отдельный скрипт, а не столбец в ``setup_groups.py --audit``. Приём
предложений включает настройка сообщества «Кто может предлагать посты»
(Управление → Настройки → Разделы → Посты; в старом интерфейсе строка
«Предлагаемые новости»: *Отключены / От всех пользователей / Только от
подписчиков*). В API её нет:

* ``groups.getById`` такого поля не отдаёт вовсе;
* ``groups.getSettings``, у которого она была полем ``suggested_privacy``,
  ВК **выпилил** — ``[3] Unknown method passed`` (проверено на всех 53);
* ``groups.edit`` её не принимает.

Остаётся косвенный читатель — поле ``can_suggest``. Но оно описывает права
**того, чьим токеном спросили**: у владельца-админа оно ``0`` и там, где
предложка работает годами. Поэтому проба гоняется токеном аккаунта, который
в сообществе **не админ и не подписчик**, и такой аккаунт скрипт выбирает
сам — по ``member_status``/``can_post`` из ответа, а не по имени токена.

⚠️ 18.09 на этом месте был сделан неверный вывод: «предложка есть только у
публичных страниц». Корреляция сходилась на 53 из 53, а причиной был
невидимый в API переключатель — опровергла его ``tuzha`` (``type=group``,
``can_suggest=1`` после переключения). Поэтому скрипт печатает **измеренное**,
а не вывод из типа сообщества.

Запуск на проде (read-only, ничего не пишет; токены не логируются):

    python3 scripts/probe_suggestions.py              # вся сеть
    python3 scripts/probe_suggestions.py mi laishevo  # выборочно
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
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("probe_suggestions")

VK_API = "https://api.vk.com/method/"
VK_VERSION = "5.131"
# user-токен: 3 вызова/сек. Берём с запасом — ошибка 6 стоит дороже паузы.
THROTTLE_SECONDS = 0.4
FIELDS = "type,wall,can_post,can_suggest,member_status,members_count"


def call(token: str, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Вызов ВК. Токен уходит в тело и никогда — в лог или в результат."""
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = VK_VERSION
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(VK_API + method, data=data)
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            return {"err": f"transport: {type(exc).__name__}"}
        if "error" in body:
            err = body["error"]
            if err.get("error_code") == 6 and attempt < 2:
                time.sleep(1.2)
                continue
            return {"err": f"[{err.get('error_code')}] {err.get('error_msg')}"}
        return {"ok": body.get("response")}
    return {"err": "retries exhausted"}


def _first_group(response: Any) -> Dict[str, Any]:
    if isinstance(response, list):
        return (response[0] if response else {}) or {}
    return ((response or {}).get("groups") or [{}])[0] or {}


async def _load_user_tokens() -> List[Tuple[str, str]]:
    """``[(имя, токен)]`` для user-токенов (community_id пуст), активные."""
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import VKToken

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(VKToken.name, VKToken.token)
                .where(VKToken.community_id.is_(None))
                .where(VKToken.is_active.is_(True))
                .order_by(VKToken.name)
            )
        ).all()
    return [(name, token) for name, token in rows]


async def _load_targets(codes: Optional[List[str]]) -> List[Tuple[str, int]]:
    from sqlalchemy import select

    from database.connection import AsyncSessionLocal
    from database.models import Region

    async with AsyncSessionLocal() as session:
        query = (
            select(Region.code, Region.vk_group_id)
            .where(Region.kind == "raion")
            .where(Region.vk_group_id.isnot(None))
            .order_by(Region.id)
        )
        if codes:
            query = query.where(Region.code.in_(codes))
        rows = (await session.execute(query)).all()
    return [(code, int(gid)) for code, gid in rows]


def pick_outsider(tokens: List[Tuple[str, str]], probe_gid: int) -> Optional[Tuple[str, str]]:
    """Токен аккаунта, который в пробном сообществе НЕ админ и НЕ подписчик.

    Выбор по измерению, а не по имени: аккаунт мог стать админом вчера.
    ``member_status=0`` и ``can_post=0`` — ровно тот, чьими глазами мы хотим
    видеть кнопку «Предложить новость».
    """
    for name, token in tokens:
        res = call(token, "groups.getById", {"group_id": probe_gid, "fields": FIELDS})
        time.sleep(THROTTLE_SECONDS)
        if "ok" not in res:
            logger.info("  %-10s пропущен: %s", name, res["err"])
            continue
        item = _first_group(res["ok"])
        if item.get("member_status") == 0 and item.get("can_post") == 0:
            return name, token
        logger.info(
            "  %-10s не годится: member_status=%s can_post=%s (свой человек в сообществе)",
            name,
            item.get("member_status"),
            item.get("can_post"),
        )
    return None


async def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("codes", nargs="*", help="коды районов; пусто — вся сеть")
    args = parser.parse_args(argv)

    targets = await _load_targets(args.codes or None)
    if not targets:
        logger.error("Нет целей")
        return 1

    tokens = await _load_user_tokens()
    if not tokens:
        logger.error("В хранилище нет user-токенов — пробу снять нечем")
        return 1

    logger.info("Ищу аккаунт-постороннего среди %d user-токенов:", len(tokens))
    outsider = pick_outsider(tokens, abs(targets[0][1]))
    if outsider is None:
        # Честный отказ вместо вердикта: измерять правами админа бессмысленно,
        # а «0 у всех» выглядело бы как «предложки нет нигде».
        logger.error(
            "Все доступные аккаунты — свои в этом сообществе. Пробу снять нечем: "
            "can_suggest у админа/подписчика не описывает права постороннего."
        )
        return 1
    name, token = outsider
    logger.info("Пробу снимаю токеном %s\n", name)

    header = f"{'код':<18}{'тип':>7}{'wall':>6}{'предложка':>12}"
    logger.info(header)
    logger.info("-" * (len(header) + 6))

    without: List[str] = []
    unknown: List[str] = []
    for code, gid in targets:
        res = call(token, "groups.getById", {"group_id": abs(gid), "fields": FIELDS})
        time.sleep(THROTTLE_SECONDS)
        if "ok" not in res:
            unknown.append(code)
            logger.info("%-18s%s", code, res["err"])
            continue
        item = _first_group(res["ok"])
        can_suggest = item.get("can_suggest")
        if can_suggest is None:
            unknown.append(code)
        elif not can_suggest:
            without.append(code)
        logger.info(
            "%-18s%7s%6s%12s",
            code,
            item.get("type", "?"),
            item.get("wall", "?"),
            "?" if can_suggest is None else ("✓" if can_suggest else "НЕТ"),
        )

    logger.info("")
    logger.info(
        "БЕЗ ПРЕДЛОЖКИ: %d — %s",
        len(without),
        ", ".join(without) or "—",
    )
    if without:
        logger.info(
            "Лечение (только руками владельца): Управление → Настройки → Разделы → "
            "Посты → «Кто может предлагать посты: Все пользователи». "
            "Перевод в публичную страницу НЕ нужен — тип сообщества тут ни при чём."
        )
    if unknown:
        # «Не измерено» держим отдельно от «нельзя» — иначе молчание сканера
        # выглядит одинаково на «всё хорошо» и на «не посмотрели» (#284).
        logger.info("НЕ ИЗМЕРЕНО: %d — %s", len(unknown), ", ".join(unknown))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
