"""Батч-чтение метрик постов ВК через ``wall.getById`` — единственное место.

**Почему модуль общий.** Такая обёртка уже была написана в
``modules/ad_cabinet/publication_stats.py`` под свои посты кабинета. Вторая
копия под чужие посты районов разошлась бы с первой молча — ровно тот класс
отказа, из-за которого D-024 сводил три копии ``_call_api`` в один
``deepseek_client``, а решение 2026-07-12 сводило VK-токены к единому
источнику. Общим здесь делается разбор ответа и нарезка батчей.

**Политика выбора токена сюда НЕ переезжает.** У кабинета она своя
(user-token админа видит просмотры своих постов, иначе community-token), у
обновления метрик района — своя (живой READ-токен из роутера). Общий модуль
получает уже готовый ``api``-объект.

**``None`` ≠ ``0``.** Поля, которых ВК не прислал, остаются ``None``: рейтинг
делит на ``(views + 1)``, и «ноль просмотров» вместо «не измеряли» подняло бы
такой пост в верхушку отбора.

**Тротлинг общий с парсером.** ``token`` — обязательный аргумент: вызовы идут
через тот же per-token лимитер и тот же учёт, что у ``VKClient`` (см.
``vk_client.enforce_token_rate_limit``). Обязательный, а не необязательный,
именно потому, что забытый аргумент означал бы залп мимо тормоза — молча и на
живом READ-токене боевого парсера.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from modules.vk_monitor.vk_client import enforce_token_rate_limit
from utils.post_utils import vk_post_datetime

logger = logging.getLogger(__name__)

Ref = Tuple[int, int]  # (owner_id со знаком, post_id)

BATCH_SIZE = 100  # потолок wall.getById


def _count(item: Dict[str, Any], key: str) -> Optional[int]:
    """``{"count": N}`` → N; поля нет → ``None`` (не ноль)."""
    block = item.get(key)
    if not isinstance(block, dict) or "count" not in block:
        return None
    try:
        return int(block["count"])
    except (TypeError, ValueError):
        return None


def parse_metrics_items(items: Iterable[Dict[str, Any]]) -> Dict[Ref, Dict[str, Any]]:
    """Разбор ответа ``wall.getById`` в словарь по ``(owner_id, post_id)``.

    Чистая функция без сети — весь разбор тестируется без ВК.
    """
    out: Dict[Ref, Dict[str, Any]] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        try:
            key: Ref = (int(item["owner_id"]), int(item["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        out[key] = {
            "views": _count(item, "views"),
            "likes": _count(item, "likes"),
            "comments": _count(item, "comments"),
            "reposts": _count(item, "reposts"),
            "published_at": vk_post_datetime(item.get("date")),
        }
    return out


def fetch_metrics_for_token(
    api: Any,
    refs: Sequence[Ref],
    *,
    token: str,
    batch_size: int = BATCH_SIZE,
) -> Dict[Ref, Dict[str, Any]]:
    """Метрики для списка постов одним токеном, батчами по ``batch_size``.

    ``api`` — уже собранный объект ``vk_api.VkApi(token=...).get_api()``.
    ``token`` — та же строка токена: по ней работает общий с парсером тормоз
    (~2.5 запроса в секунду на токен) и учёт расхода по токенам. Без него
    круг обновления метрик (78 батчей) уходил бы в ВК залпом и мог посадить
    на cooldown токен, которым живёт боевой сбор постов.

    Падение отдельного батча логируется и пропускается: обновление метрик —
    фоновая работа, и один отказ ВК не должен стоить остальных семи тысяч
    постов. Пропущенные остаются с прежними значениями (в том числе ``NULL``).
    """
    out: Dict[Ref, Dict[str, Any]] = {}
    for i in range(0, len(refs), batch_size):
        chunk = refs[i : i + batch_size]
        posts_str = ",".join(f"{o}_{p}" for o, p in chunk)
        # Тормоз ДО вызова и на каждый батч — как в VKClient.get_posts_by_ids.
        enforce_token_rate_limit(token, "wall.getById")
        try:
            resp = api.wall.getById(posts=posts_str)
        except Exception as e:  # pragma: no cover - сеть
            logger.warning("wall.getById batch failed (%d posts): %s", len(chunk), e)
            continue
        items = resp if isinstance(resp, list) else (resp or {}).get("items") or []
        out.update(parse_metrics_items(items))
    return out
