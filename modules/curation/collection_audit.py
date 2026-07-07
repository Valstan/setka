"""Fail-safe shadow-рекордер аудита сбора (ADR-0004, вариант B).

Классификатор видит ОБЕ стороны сбора: ``kept`` (пост прошёл детерминированный
фильтр — кандидат в публикацию) и ``dropped`` + причина (выброшен). Захват — на
границе сбора (``advanced_parser``), НИКОГДА не ломает публикацию:

  * **Изолированная сессия.** Своя ``AsyncSessionLocal()`` — падение не откатит сбор.
  * **Never raises.** Любое исключение глушится в WARNING (как ``recorder.py``).
  * **Gated.** OFF по умолчанию (``COLLECTION_AUDIT_SHADOW_ENABLED``) + allowlist
    регионов (``COLLECTION_AUDIT_REGION_CODES``) — обкатка на одном районе.

Причина отсева **пере-выводится** теми же чистыми функциями, что и
``_filter_post`` (``is_advertisement`` / ``check_blacklist`` / no-attachments), в
том же порядке — чтобы не трогать логику фильтра (нулевой риск для публикации).
Coupling с ``_filter_post`` отмечен в ADR-0004: при изменении фильтров синхронить.

Механические дропы (возраст / дедуп / black_id) и region_words (MVP-лимит) →
``reason=None`` → НЕ пишем: это не «пере-фильтрация», а корректный шум.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Sequence

from config.runtime import collection_audit_shadow_enabled, get_collection_audit_region_codes
from utils.post_utils import clear_copy_history, lip_of_post
from utils.text_utils import check_blacklist, is_advertisement

logger = logging.getLogger(__name__)

_MAX_AUDIT_TEXT = 3000
# Темы, которым медиа НЕ обязательно (как в _filter_post шаг 8).
_NO_ATTACH_EXEMPT_THEMES = ("novost", "reklama", "oblast")


def should_audit(region_code: str) -> bool:
    """Дёшево решить, аудируем ли регион (флаг + allowlist) — без сборки payload."""
    if not collection_audit_shadow_enabled():
        return False
    allow = get_collection_audit_region_codes()
    if allow is not None and (region_code or "").strip().lower() not in allow:
        return False
    return True


def _has_media(post: Dict[str, Any]) -> bool:
    atts = post.get("attachments")
    return bool(atts) if isinstance(atts, (list, dict)) else False


def _post_lip(post: Dict[str, Any]) -> Optional[str]:
    try:
        owner_id = int(post.get("owner_id", post.get("from_id", 0)) or 0)
        post_id = int(post.get("id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if not owner_id or not post_id:
        return None
    return lip_of_post(owner_id, post_id)


def _derive_drop_reason(post: Dict[str, Any], theme: str, region_config: Any) -> Optional[str]:
    """Пере-вывод причины content-дропа (порядок как в ``_filter_post``).

    None = механический дроп (возраст/дедуп/black_id) или region_words (MVP) →
    не пишем. Возвращаемые причины совпадают со счётчиками ``posts_filtered_*``.
    """
    text = (post.get("text") or "").strip()
    # Шаг 5: реклама (для темы reklama фильтр не срабатывает — как в _filter_post).
    if theme != "reklama" and is_advertisement(text, skip_for_reklama=False, theme=theme):
        return "advertisement"
    # Шаг 6: чёрный список слов региона.
    blacklist = getattr(region_config, "delete_msg_blacklist", None)
    if blacklist and check_blacklist(text, blacklist):
        return "blacklist_text"
    # Шаг 8: обязательные вложения для не-novost/reklama/oblast тем.
    if theme not in _NO_ATTACH_EXEMPT_THEMES and not _has_media(post):
        return "no_attachments"
    return None


def _snapshot(
    post: Dict[str, Any],
    *,
    lip: str,
    region_code: str,
    theme: str,
    decision: str,
    reason: Optional[str],
) -> Dict[str, Any]:
    owner_id = int(post.get("owner_id", post.get("from_id", 0)) or 0)
    post_id = int(post.get("id", 0) or 0)
    text = (post.get("text") or "").strip()
    return {
        "lip": lip,
        "region_code": region_code,
        "theme": theme,
        "post_text": (text[:_MAX_AUDIT_TEXT] or None),
        "post_url": f"https://vk.com/wall{owner_id}_{post_id}",
        "has_media": _has_media(post),
        "decision": decision,
        "drop_reason": reason,
    }


def build_audit_records(
    *,
    region_code: str,
    theme: str,
    region_config: Any,
    collected: Sequence[Dict[str, Any]],
    kept: Sequence[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Собрать записи аудита: все kept + content-дропы (без механических/дублей).

    Чистая функция (без БД) — тестируется отдельно. ``collected`` — сырой батч
    (до unwrap), ``kept`` — прошедшие ``_filter_post`` (уже unwrap). Матчинг по lip.
    """
    kept_lips = {lip for lip in (_post_lip(p) for p in kept) if lip}
    records: List[Dict[str, Any]] = []
    seen: set = set()
    for raw in collected:
        try:
            post = clear_copy_history(raw)  # unwrap репоста → lip как у _filter_post
        except Exception:  # pragma: no cover — битый пост не должен ронять аудит
            continue
        lip = _post_lip(post)
        if not lip or lip in seen:
            continue
        seen.add(lip)
        if lip in kept_lips:
            records.append(
                _snapshot(
                    post,
                    lip=lip,
                    region_code=region_code,
                    theme=theme,
                    decision="kept",
                    reason=None,
                )
            )
        else:
            reason = _derive_drop_reason(post, theme, region_config)
            if reason is None:
                continue  # механический дроп — не «пере-фильтрация», пропускаем
            records.append(
                _snapshot(
                    post,
                    lip=lip,
                    region_code=region_code,
                    theme=theme,
                    decision="dropped",
                    reason=reason,
                )
            )
    return records


async def record_collection_audit(
    *,
    region_code: str,
    theme: str,
    region_config: Any,
    collected: Sequence[Dict[str, Any]],
    kept: Sequence[Dict[str, Any]],
) -> None:
    """Запарковать аудит сбора (shadow). Best-effort: НИКОГДА не бросает.

    Идемпотентно по ``lip`` (first-seen wins). Вызывать ПОСЛЕ сбора/фильтрации —
    на падение аудита сбор/публикация не влияют.
    """
    try:
        if not should_audit(region_code):
            return
        records = build_audit_records(
            region_code=region_code,
            theme=theme,
            region_config=region_config,
            collected=collected or [],
            kept=kept or [],
        )
        if not records:
            return

        from sqlalchemy import select

        from database.connection import AsyncSessionLocal
        from database.models_extended import CollectedPostAudit

        lips = [r["lip"] for r in records]
        async with AsyncSessionLocal() as session:
            existing = {
                lip
                for (lip,) in (
                    await session.execute(
                        select(CollectedPostAudit.lip).where(CollectedPostAudit.lip.in_(lips))
                    )
                ).all()
            }
            added = 0
            for r in records:
                if r["lip"] in existing:
                    continue
                existing.add(r["lip"])
                session.add(CollectedPostAudit(**r))
                added += 1
            if added:
                await session.commit()
        logger.info(
            "collection audit: recorded %d (region=%s theme=%s; kept + content-drops)",
            added,
            region_code,
            theme,
        )
    except Exception:  # pragma: no cover — аудит НИКОГДА не валит сбор
        logger.warning("record_collection_audit failed (shadow, ignored)", exc_info=True)
