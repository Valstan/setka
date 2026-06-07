"""Shadow-recorder LLM-курации дайджестов (PoC, письмо brain 2026-06-07).

Фаза 1 — измеряем качество LLM-фильтра БЕЗ влияния на публикацию. После того
как дайджест опубликован текущим детерминированным путём, его вошедшие посты
паркуются в `digest_curation_runs` (status=pending). Slash-команда /curate
(Claude Code /loop) читает pending-прогоны и ставит per-post вердикт keep/drop.

Инварианты безопасности (fail-open by design):
  * **Изолированная сессия.** Recorder открывает СВОЮ `AsyncSessionLocal()`,
    не трогает транзакцию публикации — его падение не откатит и не заблокирует
    уже отправленный в VK дайджест.
  * **Never raises.** Любое исключение глушится в WARNING (как track_digest_*).
    Сбой курации = публикация всё равно прошла (current behavior).
  * **Gated.** OFF по умолчанию (`DIGEST_CURATION_SHADOW_ENABLED`), плюс
    allowlist регионов (`DIGEST_CURATION_REGION_CODES`) — для PoC 1 регион.

Гранулярность — per-post: паркуем ровно `digest.posts_included` (посты, реально
попавшие в опубликованный дайджест). Каждый из них уже прошёл все алгоритм-
фильтры, поэтому любой LLM-`drop` = чистая дельта над текущим алгоритмом.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.runtime import digest_curation_shadow_enabled, get_digest_curation_region_codes

logger = logging.getLogger(__name__)

# Текст поста режем — для вердикта релевантности этого с запасом, а строку
# держим компактной (token-economy: /curate потом читает её целиком).
_MAX_CANDIDATE_TEXT = 3000


def should_record(region_code: str) -> bool:
    """Дёшево решить, паркуем ли регион (флаг + allowlist) — без сборки payload."""
    if not digest_curation_shadow_enabled():
        return False
    allow = get_digest_curation_region_codes()
    if allow is not None and (region_code or "").strip().lower() not in allow:
        return False
    return True


def _build_candidates(
    selected_by_lip: Dict[str, Dict[str, Any]],
    posts_included: List[str],
) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    for lip in posts_included:
        post = selected_by_lip.get(lip)
        if not isinstance(post, dict):
            continue
        owner_id = int(post.get("owner_id", post.get("from_id", 0)) or 0)
        post_id = int(post.get("id", 0) or 0)
        text = (post.get("text") or "").strip()
        atts = post.get("attachments")
        has_media = bool(atts) if isinstance(atts, (list, dict)) else False
        candidates.append(
            {
                "lip": lip,
                "owner_id": owner_id,
                "post_id": post_id,
                "text": text[:_MAX_CANDIDATE_TEXT],
                "has_media": has_media,
                "url": f"https://vk.com/wall{owner_id}_{post_id}",
            }
        )
    return candidates


async def record_curation_run(
    *,
    region_code: str,
    theme: str,
    kind: str,
    selected_by_lip: Dict[str, Dict[str, Any]],
    posts_included: List[str],
    publish_result: Optional[Dict[str, Any]] = None,
) -> None:
    """Запарковать опубликованный дайджест в shadow-журнал курации.

    Best-effort: никогда не бросает, работает в изолированной сессии. Вызывать
    ПОСЛЕ успешной публикации (current path) — на падение публикация не влияет.
    """
    try:
        if not should_record(region_code):
            return
        candidates = _build_candidates(selected_by_lip, posts_included)
        if not candidates:
            return

        # Импорты внутри — чтобы модуль не тянул БД/ORM при простом импорте
        # (и чтобы гейт `should_record` отрабатывал без побочных эффектов).
        from database.connection import AsyncSessionLocal
        from database.models_extended import DigestCurationRun

        pub = publish_result or {}
        published_post_id = pub.get("post_id")
        try:
            published_post_id = int(published_post_id) if published_post_id else None
        except (TypeError, ValueError):
            published_post_id = None

        async with AsyncSessionLocal() as session:
            session.add(
                DigestCurationRun(
                    region_code=region_code,
                    theme=theme,
                    kind=kind,
                    status="pending",
                    shadow=True,
                    candidates=candidates,
                    total_count=len(candidates),
                    published_post_id=published_post_id,
                    published_url=pub.get("url"),
                )
            )
            await session.commit()
        logger.info(
            "curation shadow: parked %d posts (region=%s theme=%s kind=%s)",
            len(candidates),
            region_code,
            theme,
            kind,
        )
    except Exception:  # pragma: no cover - курация НИКОГДА не валит публикацию
        logger.warning("record_curation_run failed (shadow, ignored)", exc_info=True)
