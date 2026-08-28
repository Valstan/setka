"""
Flow B: mirror a single VK community wall to a Telegram channel, post-by-post.

Owner request: «Гоньба - жемчужина Вятки» VK wall (vk_id -218688001) → Telegram
channel @gonba_life via bot VALSTANBOT. Unlike Flow A (bulletin mirror), this is a
1:1 wall mirror: each new community post is reposted with its media, ad-filtered.

Patterned on ``modules.copy_setka_network`` (VK→VK wall mirror): same
cooldown-aware token selection and WorkTable lip-dedup, but the target is a
Telegram channel (``modules.publisher.telegram_repost``) instead of VK walls.
Dedup lives in Postgres (WorkTable region_code="gonba", theme="telegram") — not
Redis — so a cache flush can never re-spam the whole wall into a live channel.

Повтор ограничен с двух сторон, и обе границы нужны:

- **частичная доставка не повторяется вовсе** — если в канал ушло хоть что-то
  (``delivered``), пост считается отправленным: повтор дал бы дубль, а не
  доставку (инцидент 27-28.08 в @gonba_life, PR #545);
- **полный провал повторяется, но не бесконечно** — ``GONBA_MAX_SEND_ATTEMPTS``
  (по умолчанию 3) поверх возрастного потолка. Счётчик попыток лежит в
  ``WorkTable.failed_attempts`` той же строки (миграция 089), чтобы двигаться
  одним коммитом с курсором.
"""

from __future__ import annotations

import asyncio
import copy as copy_lib
import logging
import time
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

WALL_FETCH_COUNT = 10
GONBA_LIP_HISTORY_MAX = 50  # larger than copy_setka (fires often, bursty wall)


def _empty_stats() -> Dict[str, int]:
    return {
        "scanned": 0,
        "sent": 0,
        "sent_partial": 0,
        "given_up": 0,
        "skipped_seen": 0,
        "skipped_old": 0,
        "skipped_ads": 0,
    }


async def execute_gonba_telegram_mirror(
    session: AsyncSession,
    *,
    community_id: Optional[int] = None,
    test_mode: bool = False,
) -> Dict[str, Any]:
    from database.models import Community
    from modules.publisher.telegram_repost import (
        clean_text_for_telegram,
        repost_to_telegram,
        resolve_media,
    )
    from modules.publisher.telegram_repost_config import (
        get_gonba_community_id,
        get_gonba_max_post_age_hours,
        get_gonba_max_posts_per_run,
        get_gonba_max_send_attempts,
        get_telegram_extra_hashtags,
        telegram_repost_disabled,
    )
    from modules.vk_monitor.vk_client import VKClient
    from modules.vk_monitor.vk_client_async import VKClientAsync
    from utils.post_utils import clear_copy_history, lip_of_post
    from utils.text_utils import is_advertisement

    stats = _empty_stats()

    if telegram_repost_disabled():
        logger.info("TELEGRAM_REPOST_DISABLED — Гоньба-зеркало пропущено")
        return {"success": False, "skipped": "disabled", "stats": stats}

    cid = community_id or get_gonba_community_id()
    community = (
        (await session.execute(select(Community).where(Community.id == cid))).scalars().first()
    )
    if not community:
        return {"success": False, "error": f"community {cid} not found", "stats": stats}
    if not community.telegram_channel or not community.telegram_bot:
        return {
            "success": False,
            "error": f"community {cid} has no telegram_channel/telegram_bot",
            "stats": stats,
        }

    # Cooldown-aware READ token (миграция 014). НЕ fallback на полный список —
    # иначе можно взять заблокированный токен (инцидент 2026-05-27).
    from modules.vk_token_router import get_active_parse_tokens

    parse_tokens = await get_active_parse_tokens(session)
    if not parse_tokens:
        return {"success": False, "error": "No active VK READ tokens", "stats": stats}
    parse_token = next(iter(parse_tokens.values()))
    vk = VKClient(parse_token)

    source_owner_id = int(community.vk_id)
    posts: List[Dict[str, Any]] = await asyncio.to_thread(
        vk.get_wall_posts, source_owner_id, WALL_FETCH_COUNT, 0
    )
    stats["scanned"] = len(posts)
    if not posts:
        return {"success": True, "message": "no posts on wall", "stats": stats}

    # WorkTable lip-dedup (Postgres).
    from database.models_extended import WorkTable

    wt = (
        (
            await session.execute(
                select(WorkTable).where(
                    WorkTable.region_code == "gonba", WorkTable.theme == "telegram"
                )
            )
        )
        .scalars()
        .first()
    )
    if not wt:
        wt = WorkTable(region_code="gonba", theme="telegram", lip=[], hash=[])
        session.add(wt)
        await session.commit()
        await session.refresh(wt)

    known: Set[str] = set(wt.lip or [])
    # Счётчик полных провалов по посту (миграция 089): {lip: попыток}.
    attempts: Dict[str, int] = {str(k): int(v) for k, v in (wt.failed_attempts or {}).items()}
    prev_attempts = dict(attempts)
    max_attempts = get_gonba_max_send_attempts()
    max_age = int(get_gonba_max_post_age_hours() * 3600)
    now_ts = int(time.time())
    cap = get_gonba_max_posts_per_run()

    # Все lip текущего окна стены — по ним чистится счётчик (см. _prune_attempts).
    wall_lips: Set[str] = {
        lip_of_post(int(p.get("owner_id", source_owner_id)), int(p["id"]))
        for p in posts
        if p.get("id") is not None
    }

    def _prune_attempts() -> Dict[str, int]:
        """Оставить счётчик только по постам, которые ещё имеет смысл пробовать.

        Пост, выпавший из окна стены или уже помеченный отправленным, повторяться
        не будет — держать по нему счётчик незачем. Благодаря этому словарь
        самоочищается и не растёт, отдельной чистки не нужно.
        """
        return {k: v for k, v in attempts.items() if k in wall_lips and k not in known}

    # Oldest-first so the channel timeline reads chronologically; skip
    # pinned/duplicate/old/ads. Cap per run to avoid floods.
    posts_sorted = sorted(posts, key=lambda p: p.get("date", 0))
    fresh: List[Dict[str, Any]] = []
    for p in posts_sorted:
        pid = p.get("id")
        if pid is None:
            continue
        oid = int(p.get("owner_id", source_owner_id))
        lip = lip_of_post(oid, int(pid))
        if lip in known:
            stats["skipped_seen"] += 1
            continue
        if max_age > 0 and now_ts - int(p.get("date") or 0) > max_age:
            stats["skipped_old"] += 1
            continue
        # Ad filter (same helper as the parser pipeline): VK marked_as_ads flag
        # is checked separately from the text-based heuristics.
        if p.get("marked_as_ads") or is_advertisement(p.get("text") or "", theme="novost"):
            stats["skipped_ads"] += 1
            # Mark as seen so we don't re-evaluate the ad every run.
            known.add(lip)
            continue
        fresh.append(p)
        if len(fresh) >= cap:
            break

    if not fresh:
        pruned = _prune_attempts()
        if not test_mode and (known != set(wt.lip or []) or pruned != prev_attempts):
            wt.lip = list(known)[-GONBA_LIP_HISTORY_MAX:]
            wt.failed_attempts = pruned or None
            await session.commit()
        return {"success": True, "message": "nothing new to mirror", "stats": stats}

    extra_tags = get_telegram_extra_hashtags(community.telegram_channel)
    sent_lips: List[str] = []
    errors: List[str] = []

    def _note_failure(lip: str, reason: str) -> None:
        """Полный провал (в канал не ушло ничего) — считаем попытку и, если их
        стало ``max_attempts``, перестаём пробовать этот пост навсегда.

        Повторять то, что не доставлено, правильно — но не бесконечно: раньше
        потолком был только возраст поста, до ~96 попыток за 48 часов.
        """
        tries = attempts.get(lip, 0) + 1
        attempts[lip] = tries
        if 0 < max_attempts <= tries:
            known.add(lip)  # сдались — помечаем отправленным, чтобы не крутить дальше
            attempts.pop(lip, None)
            stats["given_up"] += 1
            logger.error(
                "Гоньба-зеркало: пост %s не доставлен за %d попыток (%s) — "
                "перестаём пробовать, в канал он не попадёт",
                lip,
                tries,
                reason,
            )
            errors.append(f"{lip}: сдались после {tries} попыток ({reason})")
        else:
            limit = f" из {max_attempts}" if max_attempts > 0 else ""
            errors.append(f"{lip}: {reason} (попытка {tries}{limit})")

    async with VKClientAsync(parse_token) as tg_vk:
        for p in fresh:
            oid = int(p.get("owner_id", source_owner_id))
            lip = lip_of_post(oid, int(p["id"]))
            try:
                effective = clear_copy_history(copy_lib.deepcopy(p))
                text = clean_text_for_telegram(
                    effective.get("text") or "", extra_hashtags=extra_tags
                )
                media = await resolve_media(effective, tg_vk)
                out = await repost_to_telegram(
                    community.telegram_bot,
                    community.telegram_channel,
                    text,
                    media,
                    test_mode=test_mode,
                )
                if out.get("success"):
                    sent_lips.append(lip)
                    stats["sent"] += 1
                    attempts.pop(lip, None)
                elif out.get("delivered"):
                    # Часть поста уже в канале (типовой случай: альбом не ушёл,
                    # а длинный текст ушёл отдельным сообщением). Повтор такого
                    # поста даёт не доставку, а ДУБЛЬ — и так каждые 30 минут,
                    # пока пост не состарится. Считаем отправленным, потерю
                    # медиа фиксируем в логе и в errors, а не молча.
                    sent_lips.append(lip)
                    stats["sent_partial"] += 1
                    attempts.pop(lip, None)
                    logger.warning(
                        "Гоньба-зеркало: пост %s доставлен частично — помечен отправленным, "
                        "чтобы не дублировать его в канале; часть содержимого потеряна",
                        lip,
                    )
                    errors.append(f"{lip}: доставлено частично (детали — в логе Telegram-вызовов)")
                else:
                    _note_failure(lip, str(out.get("error") or "send failed"))
            except Exception as e:
                logger.exception("Гоньба-зеркало: ошибка на посте %s", lip)
                _note_failure(lip, str(e))

    # Persist progress: ad-skips + give-ups (added to `known`), successfully sent
    # posts, and the failure counter — всё одним коммитом: «сдались после N» и
    # «помечен отправленным» не должны разъехаться при падении между записями.
    # test_mode is a side-effect-free dry-run: never touch the persistent cursor
    # (otherwise a dry-run on prod would mark posts as seen and the live beat run
    # would skip them).
    for lip in sent_lips:
        known.add(lip)
    pruned = _prune_attempts()
    if not test_mode and (sent_lips or known != set(wt.lip or []) or pruned != prev_attempts):
        prev = list(wt.lip or [])
        merged = [lip for lip in prev if lip in known] + [lip for lip in known if lip not in prev]
        wt.lip = merged[-GONBA_LIP_HISTORY_MAX:]
        wt.failed_attempts = pruned or None
        await session.commit()

    return {
        "success": len(errors) == 0,
        "posts_published": stats["sent"],
        "errors": errors[:20],
        "stats": stats,
    }
