#!/usr/bin/env python3
"""Живой VK-probe: что стало с сообществами, которые авто-политика вынесла как T1-dormant.

**Зачем.** Политика `dormant_t1_auto` (одобрена brain 2026-06-30, построена 07-05)
сама решает, что сообщество мертво, и ставит `is_active=false`. Условие brain'а —
после первого месячного цикла оценить исходы и оформить «3 судьбы» письмом (#009,
measure-before-promote).

**Почему для этого нужен отдельный probe, а не SQL.** Оценить исходы по нашей БД
нельзя ни одним запросом: weekly-recheck (`tasks/discovery_tasks.recheck_*`) ходит
только по `is_active=True`, и вынесенная строка больше не проверяется НИКОГДА.
Замерено 2026-08-21: у всех 48 вынесенных `checked_at` не сдвинулся после
`disabled_at` ни разу. Docstring recheck'а объясняет пропуск тем, что строку
пометил «модератор» — для человека это верно (он посмотрел и решил), но с 07-05
её помечает автомат, и его вердикт как раз и есть то, что нужнее всего
перепроверить. Итог: ежемесячный дайджест сообщает, КОГО вынесли, и по построению
не может сообщить, ВЕРНО ЛИ. Единственный способ узнать — сходить в VK самим.

**Что делает (read-only, без сайд-эффектов):**
  * читает строки `communities` с нужным `disabled_reason` (по умолчанию `dormant_t1_auto`);
  * для каждой зовёт `wall.get` через `client.api_call` (нужен raw `error_code`,
    чтобы отличить «группа удалена» от пустой стены — та же причина, что в
    `modules/discovery/health_check.py`);
  * раскладывает на ТРИ СУДЬБЫ и печатает сводку.

**Три судьбы:**
  1. `spit`      — новых постов после `disabled_at` нет. Вердикт автомата держится.
  2. `ozhil`     — есть пост ПОЗЖЕ `disabled_at`. Сообщество ожило, пока мы на него
                   не смотрели: ложное срабатывание не в момент выноса, а в том, что
                   вынос необратим по построению.
  3. `nedostupen`— VK отвечает ошибкой 15/18/100/203. Другая судьба: не «спит», а
                   удалено/забанено/закрыто — и тогда вынос был верен по сути, но по
                   неверной причине (записан `dormant`, а на деле `dead`).

**В БД НИЧЕГО НЕ ПИШЕТ.** Ни `checked_at`, ни `health_status`, ни `is_active`.
Это замер, а не починка: что делать с находками — решает владелец.

**Ловушка времени (проверена).** `last_post_at`/`disabled_at` в БД — наивный UTC,
а `now()` в psql на проде отдаёт MSK. VK в `wall.get` отдаёт unix-timestamp (UTC).
Скрипт сравнивает всё в наивном UTC и печатает зону явно, чтобы разница в 3 часа
не превратилась в ложное «ожил».

Примеры (на проде через `ssh sarafan`):

    # все вынесенные, окно wall.get = 5 постов
    python3 scripts/probe_dormant_t1_outcomes.py

    # быстрая проба на пяти строках
    python3 scripts/probe_dormant_t1_outcomes.py --limit 5

    # машинный вывод для письма
    python3 scripts/probe_dormant_t1_outcomes.py --json > /tmp/fates.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import select

from database.connection import AsyncSessionLocal
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


def _newest_post_dt(items: List[Dict[str, Any]]) -> Optional[datetime]:
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


async def _wall_get(client: VKClient, owner_id: int, count: int) -> Dict[str, Any]:
    return await asyncio.to_thread(
        client.api_call,
        "wall.get",
        {"owner_id": owner_id, "count": count, "extended": 0},
    )


def _classify(
    community: Community,
    resp: Dict[str, Any],
) -> Outcome:
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
    newest = _newest_post_dt(items)

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


async def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--reason", default="dormant_t1_auto", help="disabled_reason для выборки")
    ap.add_argument("--limit", type=int, default=0, help="0 = все")
    ap.add_argument("--count", type=int, default=5, help="сколько постов запрашивать у wall.get")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    args = ap.parse_args()

    from tasks.discovery_tasks import _pick_parse_token

    token = _pick_parse_token()
    if not token:
        print("нет VK parse-токена (VK_TOKENS пуст или все в cooldown)")
        return 1
    client = VKClient(token=token)

    async with AsyncSessionLocal() as session:
        stmt = (
            select(Community)
            .where(Community.disabled_reason == args.reason)
            .order_by(Community.disabled_at, Community.id)
        )
        if args.limit:
            stmt = stmt.limit(args.limit)
        rows = list((await session.execute(stmt)).scalars())

    if not rows:
        print(f"строк с disabled_reason={args.reason!r} нет — нечего мерить")
        return 0

    outcomes: List[Outcome] = []
    for community in rows:
        vk_id = abs(int(community.vk_id or 0))
        if vk_id == 0:
            outcomes.append(
                Outcome(
                    community_id=community.id,
                    vk_id=0,
                    name=(community.name or "")[:60],
                    disabled_at=community.disabled_at,
                    last_post_at_db=community.last_post_at,
                    newest_post_at_vk=None,
                    fate=FATE_UNKNOWN,
                    note="vk_id пуст",
                )
            )
            continue
        resp = await _wall_get(client, -vk_id, args.count)
        outcomes.append(_classify(community, resp))

    if args.json:
        print(json.dumps([o.as_dict() for o in outcomes], ensure_ascii=False, indent=2))
        return 0

    buckets: Dict[str, List[Outcome]] = {}
    for o in outcomes:
        buckets.setdefault(o.fate, []).append(o)

    print(f"── ТРИ СУДЬБЫ вынесенных по {args.reason} ──")
    print(f"Всего строк: {len(outcomes)}. Время везде наивный UTC (в psql now() — MSK, не путать).")
    print("В БД ничего не записано: это замер.\n")

    for fate in (FATE_ASLEEP, FATE_REVIVED, FATE_UNREACHABLE, FATE_UNKNOWN):
        got = buckets.get(fate) or []
        share = f"{len(got) / len(outcomes):.0%}" if outcomes else "—"
        print(f"{len(got):>3} ({share:>4})  {FATE_TITLES[fate]}")

    for fate in (FATE_REVIVED, FATE_UNREACHABLE, FATE_UNKNOWN):
        got = buckets.get(fate) or []
        if not got:
            continue
        print(f"\n── {FATE_TITLES[fate]} ──")
        for o in got:
            when = o.newest_post_at_vk.strftime("%Y-%m-%d") if o.newest_post_at_vk else "—"
            dis = o.disabled_at.strftime("%Y-%m-%d") if o.disabled_at else "—"
            code = f" err={o.error_code}" if o.error_code else ""
            head = f"  id={o.community_id:<6} vk={o.vk_id:<12} вынесен={dis}"
            print(f"{head} новейший={when}{code}  {o.name}")
            if o.note:
                print(f"      {o.note}")

    revived = len(buckets.get(FATE_REVIVED) or [])
    if revived:
        print(
            f"\n⚠️ {revived} сообществ ожили после выноса. Вынос необратим по построению "
            "(recheck ходит только по is_active=True), поэтому сами мы бы этого не узнали никогда."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
