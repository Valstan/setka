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


def vantage_of(item: Dict[str, Any]) -> Optional[str]:
    """Чьими глазами получился этот ответ: ``outsider`` / ``subscriber`` / None.

    * ``outsider`` — не подписан и писать не может: ровно тот, для кого мы и
      спрашиваем «видна ли кнопка «Предложить новость»»;
    * ``subscriber`` — подписан, но не пишет на стену. Ответ слабее: он
      говорит про права подписчика, а настройка различает «всех» и «только
      подписчиков», то есть у постороннего может быть иначе;
    * ``None`` — админ/редактор (``can_post=1``). Его ``can_suggest`` равен
      нулю всегда и не значит ничего.
    """
    member = item.get("member_status")
    can_post = item.get("can_post")
    if can_post == 1:
        return None
    if member == 0:
        return "outsider"
    if member == 1:
        return "subscriber"
    return None


def measure(
    tokens: List[Tuple[str, str]], gid: int, prefer: Optional[str]
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Снять ``can_suggest`` по сообществу лучшим доступным взглядом.

    Возвращает ``(item, vantage, token_name)``; ``vantage is None`` — мерить
    было нечем (все доступные аккаунты — свои в этом сообществе).

    **Почему выбор делается для КАЖДОГО сообщества, а не один раз на прогон.**
    Первая версия выбирала «постороннего» по первому району и дальше считала
    его посторонним везде. На `vp` это развалилось: аккаунт оказался
    подписчиком именно там — и скрипт, вместо того чтобы измерить остальные 52,
    отказался мерить вообще. Членство — свойство пары (аккаунт, сообщество),
    а не аккаунта.
    """
    ordered = sorted(tokens, key=lambda t: t[0] != prefer)
    fallback: Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]] = (None, None, None)
    for name, token in ordered:
        res = call(token, "groups.getById", {"group_id": abs(gid), "fields": FIELDS})
        time.sleep(THROTTLE_SECONDS)
        if "ok" not in res:
            continue
        item = _first_group(res["ok"])
        vantage = vantage_of(item)
        if vantage == "outsider":
            return item, vantage, name
        if vantage == "subscriber" and fallback[1] is None:
            # Держим как запасной вариант, но ищем дальше настоящего постороннего.
            fallback = (item, vantage, name)
    return fallback


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

    logger.info(
        "user-токенов в хранилище: %d — взгляд выбирается по каждому сообществу\n", len(tokens)
    )

    header = f"{'код':<18}{'тип':>7}{'wall':>6}{'предложка':>12}  чьими глазами"
    logger.info(header)
    logger.info("-" * (len(header) + 6))

    without: List[str] = []
    unknown: List[str] = []
    subscriber_only: List[str] = []
    prefer: Optional[str] = None
    for code, gid in targets:
        item, vantage, token_name = measure(tokens, gid, prefer)
        if vantage == "outsider":
            # Следующему сообществу пробуем этот же токен первым: обычно он и
            # там посторонний, и тогда прогон стоит один вызов на район.
            prefer = token_name
        if item is None or vantage is None:
            unknown.append(code)
            logger.info("%-18s%7s%6s%12s  %s", code, "?", "?", "?", "мерить нечем — все свои")
            continue
        can_suggest = item.get("can_suggest")
        if can_suggest is None:
            unknown.append(code)
        elif not can_suggest:
            without.append(code)
        if vantage == "subscriber" and can_suggest:
            # «Подписчик может» не означает «может любой»: настройка различает
            # «От всех пользователей» и «Только от подписчиков».
            subscriber_only.append(code)
        logger.info(
            "%-18s%7s%6s%12s  %s",
            code,
            item.get("type", "?"),
            item.get("wall", "?"),
            "?" if can_suggest is None else ("✓" if can_suggest else "НЕТ"),
            "посторонний" if vantage == "outsider" else f"подписчик ({token_name})",
        )

    logger.info("")
    logger.info(
        "БЕЗ ПРЕДЛОЖКИ: %d — %s",
        len(without),
        ", ".join(without) or "—",
    )
    if subscriber_only:
        logger.info(
            "✓ но глазами ПОДПИСЧИКА (про постороннего не доказано): %d — %s",
            len(subscriber_only),
            ", ".join(subscriber_only),
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
