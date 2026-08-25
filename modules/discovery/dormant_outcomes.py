"""Судьба сообщества, вынесенного dormant-политикой T1: спит / ожил / недоступен.

Вынесено из ``scripts/probe_dormant_t1_outcomes.py`` (замер 2026-08-21, «3 судьбы»),
когда та же логика понадобилась ежемесячной ре-проверке
(``tasks.discovery_tasks.dormant_recheck_disabled``). Копировать было нельзя: две
копии расходятся молча, а тут расхождение означало бы, что замер и рутина считают
разное одним словом.

**Зачем ре-проверка вообще** (рекомендация brain 2026-08-22, пул
[#182](../../../brain_matrica/cross-project-ideas/ideas/)): автоматический вердикт
выводит свой предмет **из области наблюдения** — вынесенное сообщество перестаёт
опрашиваться, и ошибка выноса становится ненаблюдаемой по построению. Условие, при
котором «вынес и забыл» было приемлемо, звучало «вердикт ставит человек»; с июля его
ставит автомат. Отсюда правило: **автоматический вердикт, выводящий предмет из
наблюдения, не должен быть неотзывным.**

Замер 2026-08-21 на 48 строках: 46 спят, 2 ожили (детский дом и детская секция
каратэ), 0 недоступны.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from database.models import Community
from modules.discovery.health_check import DEAD_ERROR_CODES
from modules.vk_monitor.vk_client import VKClient

FATE_ASLEEP = "spit"
FATE_REVIVED = "ozhil"
FATE_UNREACHABLE = "nedostupen"
FATE_UNKNOWN = "neyasno"

FATE_TITLES = {
    FATE_ASLEEP: "СПИТ — новых постов после выноса нет, вердикт автомата держится",
    FATE_REVIVED: "ОЖИЛ — есть пост позже disabled_at, а мы на него больше не смотрели",
    FATE_UNREACHABLE: "НЕДОСТУПЕН — удалено/забанено/закрыто (а вынесен как dormant)",
    FATE_UNKNOWN: "НЕЯСНО — transient-ошибка VK, судьба не установлена (повторить)",
}


@dataclass
class Outcome:
    community_id: int
    vk_id: int
    name: str
    disabled_at: Optional[datetime]
    last_post_at_db: Optional[datetime]
    newest_post_at_vk: Optional[datetime]
    fate: str
    error_code: Optional[int] = None
    note: str = ""

    def as_dict(self) -> Dict[str, Any]:
        def iso(v: Optional[datetime]) -> Optional[str]:
            return v.isoformat() if v else None

        return {
            "community_id": self.community_id,
            "vk_id": self.vk_id,
            "name": self.name,
            "disabled_at_utc": iso(self.disabled_at),
            "last_post_at_db_utc": iso(self.last_post_at_db),
            "newest_post_at_vk_utc": iso(self.newest_post_at_vk),
            "fate": self.fate,
            "error_code": self.error_code,
            "note": self.note,
        }


def newest_post_dt(items: List[Dict[str, Any]]) -> Optional[datetime]:
    """Самый свежий пост стены в наивном UTC.

    Берём max по всем items, а не items[0]: первым VK отдаёт ЗАКРЕПЛЁННЫЙ пост,
    который часто старше остальных. Читать items[0] — классический способ
    объявить живое сообщество мёртвым.
    """
    stamps = [int(it.get("date") or 0) for it in items if it.get("date")]
    stamps = [s for s in stamps if s > 0]
    if not stamps:
        return None
    return datetime.utcfromtimestamp(max(stamps))


async def wall_get(client: VKClient, owner_id: int, count: int) -> Dict[str, Any]:
    return await asyncio.to_thread(
        client.api_call,
        "wall.get",
        {"owner_id": owner_id, "count": count, "extended": 0},
    )


def classify(community: Community, resp: Dict[str, Any]) -> Outcome:
    """Судьба по ответу ``wall.get``.

    Время: ``disabled_at``/``last_post_at`` в БД — наивный UTC, ``date`` у VK —
    unix-timestamp UTC. Сравниваем в наивном UTC, без tz-арифметики (на проде
    ``now()`` в psql — MSK, и смешение стоило бы трёх часов сдвига).
    """
    vk_id = abs(int(community.vk_id or 0))
    base = dict(
        community_id=community.id,
        vk_id=vk_id,
        name=(community.name or "")[:60],
        disabled_at=community.disabled_at,
        last_post_at_db=community.last_post_at,
    )

    if isinstance(resp, dict) and resp.get("error"):
        err = resp.get("error") or {}
        code = int(err.get("error_code") or 0)
        msg = (err.get("error_msg") or "").strip()
        if code in DEAD_ERROR_CODES:
            return Outcome(
                **base,
                newest_post_at_vk=None,
                fate=FATE_UNREACHABLE,
                error_code=code,
                note=msg,
            )
        return Outcome(
            **base,
            newest_post_at_vk=None,
            fate=FATE_UNKNOWN,
            error_code=code,
            note=f"transient: {msg}" if msg else "transient",
        )

    items = (resp or {}).get("items") or []
    newest = newest_post_dt(items)

    if newest is None:
        return Outcome(
            **base,
            newest_post_at_vk=None,
            fate=FATE_ASLEEP,
            note="стена пуста или посты без даты",
        )

    disabled_at = community.disabled_at
    if disabled_at is not None and newest > disabled_at:
        delta_days = (newest - disabled_at).days
        return Outcome(
            **base,
            newest_post_at_vk=newest,
            fate=FATE_REVIVED,
            note=f"пост через {delta_days} сут после выноса",
        )

    return Outcome(
        **base,
        newest_post_at_vk=newest,
        fate=FATE_ASLEEP,
        note="последний пост старше выноса",
    )
