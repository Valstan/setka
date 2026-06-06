#!/usr/bin/env python3
"""Живой VK-probe: даёт ли `stats.get` просмотры/охват сообщества (R-probe графика роста).

Probe-before-build (#020 / G19): ДО постройки метрики «рост просмотров/охвата» на
сравнительном графике подписчиков проверяем на проде, что VK реально отдаёт по
`stats.get`. VK **молча запрещает по роли/токену**: статистика сообщества доступна
только токену с правами `stats` И администратору сообщества, и не для всех групп.
Уже проходили это с `wall.edit` на предложке (G19) — поэтому пробуем вживую.

Что делает (read-only, без сайд-эффектов):
  * берёт user-токен и community-токены через `load_vk_routing`;
  * для цели (`--group` или все community-группы) зовёт `stats.get` за последние
    `--days` дней — сначала user-токеном (если он админ + scope `stats`), затем
    community-токеном;
  * печатает, какие поля пришли (visitors/reach/views и пр.) либо код ошибки VK.

Подписчики (`members_count`) уже доказаны живым кодом (snapshot-коллектор в
проде) — этот probe только про ПРОСМОТРЫ/ОХВАТ, которые директива пометила
probe-gated.

Примеры (на проде через `ssh setka`):

    # все community-группы, окно 7 дней
    python3 scripts/probe_stats_get_capability.py

    # конкретная группа, окно 14 дней
    python3 scripts/probe_stats_get_capability.py --group -218688001 --days 14
"""

from __future__ import annotations

import argparse
import asyncio
from typing import Any, List, Optional


def _summarize_stats(rows: Any) -> str:
    """Короткая сводка того, что вернул stats.get (структура зависит от extended)."""
    if not rows:
        return "пустой ответ (нет данных за период / нет доступа к метрикам)"
    if isinstance(rows, dict):
        rows = [rows]
    keys: set = set()
    sample_period = None
    last = None
    for r in rows:
        if not isinstance(r, dict):
            continue
        keys.update(r.keys())
        if sample_period is None:
            sample_period = {k: r.get(k) for k in ("period_from", "period_to") if k in r}
        last = r
    fields = sorted(k for k in keys if k not in ("period_from", "period_to"))
    has_views = any("view" in k for k in fields)
    has_reach = any("reach" in k for k in fields)
    has_visit = any("visit" in k for k in fields)
    flags = []
    if has_views:
        flags.append("views✅")
    if has_reach:
        flags.append("reach✅")
    if has_visit:
        flags.append("visitors✅")
    detail = ""
    if last:
        # вытащим первые скалярные значения для наглядности
        scal = {
            k: v
            for k, v in last.items()
            if isinstance(v, (int, float)) and k not in ("period_from", "period_to")
        }
        detail = f"; пример точки: {scal}" if scal else ""
    return f"{len(rows)} точек; поля={fields}; {' '.join(flags) or 'метрик роста нет'}{detail}"


def _call_stats(api, gid_abs: int, days: int) -> Any:
    """stats.get за последние `days` дней с дневным интервалом, extended для reach/views.

    `date_from`/`date_to` deprecated с 5.86 → используем unix `timestamp_from`/`_to`.
    """
    import time

    now = int(time.time())
    return api.stats.get(
        group_id=gid_abs,
        timestamp_from=now - days * 86400,
        timestamp_to=now,
        interval="day",
        intervals_count=days,
        extended=1,
    )


async def _probe_group(
    gid_abs: int, user_token: Optional[str], comm_token: Optional[str], days: int
):
    import vk_api
    from vk_api.exceptions import ApiError

    print(f"══ группа -{gid_abs} ══")
    tried = False
    for label, tok in (("user-токен", user_token), ("community-токен", comm_token)):
        if not tok:
            continue
        tried = True
        api = vk_api.VkApi(token=tok).get_api()
        try:
            rows = await asyncio.to_thread(_call_stats, api, gid_abs, days)
            print(f"  ✅ stats.get ({label}): {_summarize_stats(rows)}")
        except ApiError as e:
            hint = ""
            if e.code == 15:
                hint = " (Access denied — не админ группы / нет scope stats)"
            elif e.code == 27:
                hint = " (group auth — метод недоступен community-токену)"
            elif e.code == 5:
                hint = " (auth failed — токен невалиден/IP)"
            print(f"  ❌ stats.get ({label}) → [{e.code}] {e}{hint}")
        except Exception as e:  # noqa: BLE001 — probe печатает любую боль
            print(f"  ⚠️ stats.get ({label}) → {type(e).__name__}: {e}")
    if not tried:
        print("  — нет ни user-, ни community-токена для пробы")


async def main() -> int:
    ap = argparse.ArgumentParser(description="VK-probe stats.get (views/reach) для графика роста")
    ap.add_argument("--group", type=int, default=None, help="VK group id (можно с минусом)")
    ap.add_argument("--days", type=int, default=7, help="окно в днях (по умолчанию 7)")
    ap.add_argument("--limit", type=int, default=5, help="сколько групп пробовать без --group")
    args = ap.parse_args()

    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    print(
        f"🔎 user-токен: {'есть' if user_token else 'нет'}; "
        f"community-токенов: {len(community_tokens)}; окно: {args.days}д\n"
    )

    if args.group is not None:
        gid = abs(int(args.group))
        await _probe_group(gid, user_token, community_tokens.get(gid), args.days)
    else:
        gids: List[int] = sorted(community_tokens.keys())[: args.limit]
        if not gids:
            print("⚠️ нет community-групп для пробы — укажи --group")
            return 1
        for gid in gids:
            await _probe_group(gid, user_token, community_tokens.get(gid), args.days)
            print()

    print("── ИТОГ ──")
    print(
        " • Если хоть где-то stats.get отдал views/reach — метрика просмотров достижима (апгрейд)."
    )
    print(" • Если везде [15]/[27]/пусто — MVP только по подписчикам; просмотры VK не даёт.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
