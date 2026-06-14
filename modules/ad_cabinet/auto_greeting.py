"""Авто-приветствие рекламодателю (улучшение отклика, раунд 1 — 2026-06-13).

Цель — мгновенный первый отклик: как только в кабинет падает новая рекламная
заявка (предложка/ЛС) в разрешённом сообществе, рекламодателю сразу уходит
короткое приветствие («получили ваше предложение, условия такие-то…»), не
дожидаясь, пока оператор откроет карточку. Это резко улучшает «отклик между
владельцем и рекламодателем» (директива brain 2026-06-12).

Безопасность: **off по умолчанию**, гейт в #008-стиле — фича работает только если
заданы оба env:
  * ``AD_AUTO_GREETING_COMMUNITIES`` — allowlist community vk_id (per-community
    включатель владельца), пусто → выключено; **``*`` или ``all`` = все
    сообщества** (включая будущие — без перечисления id, решение владельца
    2026-06-14 «на все группы»);
  * ``AD_AUTO_GREETING_TEXT`` (плейсхолдеры {author_name}/{community_name}) ИЛИ
    активный шаблон категории ``ad_greeting`` — текст приветствия.

Идемпотентность: приветствуем один раз (``greeting_sent_at IS NULL``), только
свежие заявки (anti-backlog окно ``FRESH_HOURS``), только где писать можно
(``can_message``, не группа-автор). VK-отправка инъектируема (``send``) для тестов;
never-raises per-request.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, Optional, Set

from sqlalchemy import select

from database.models import AdRequest, MessageTemplate
from modules.ad_cabinet.interaction_log import log_interaction
from modules.ad_cabinet.message_builder import render

logger = logging.getLogger(__name__)

GREETING_CATEGORY = "ad_greeting"
FRESH_HOURS = 6  # не приветствуем старый бэклог при первом включении
MAX_PER_RUN = 50


def _env_allow_all() -> bool:
    """``*`` / ``all`` в allowlist-env = приветствовать во всех сообществах (вкл. будущие)."""
    return os.getenv("AD_AUTO_GREETING_COMMUNITIES", "").strip().lower() in {"*", "all"}


def _env_allowlist() -> Set[int]:
    raw = os.getenv("AD_AUTO_GREETING_COMMUNITIES", "")
    out: Set[int] = set()
    for part in raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.add(int(part))
        except ValueError:
            logger.warning("auto-greeting: нечисловой community id в allowlist: %r", part)
    return out


async def run_auto_greeting(
    *,
    session_factory: Optional[Callable] = None,
    send: Optional[Callable[[int, int, str], Any]] = None,
    allowlist: Optional[Set[int]] = None,
    allow_all: Optional[bool] = None,
    template_text: Optional[str] = None,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Поприветствовать рекламодателей в свежих новых заявках. Возвращает счётчики."""
    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or datetime.utcnow()
    # allow_all=True (env ``*``/``all``) → без фильтра сообществ; иначе — allowlist по vk_id.
    if allowlist is None and allow_all is None:
        allow_all = _env_allow_all()
        allowlist = set() if allow_all else _env_allowlist()
    else:
        allow_all = bool(allow_all)
        allowlist = set(allowlist or ())
    if not allow_all and not allowlist:
        return {"greeted": 0, "checked": 0, "skipped": "disabled"}

    # Текст: env-приоритет, иначе активный шаблон ad_greeting.
    if template_text is None:
        template_text = os.getenv("AD_AUTO_GREETING_TEXT") or None

    async with session_factory() as session:
        if template_text is None:
            tpl = (
                (
                    await session.execute(
                        select(MessageTemplate)
                        .where(
                            MessageTemplate.category == GREETING_CATEGORY,
                            MessageTemplate.is_active.is_(True),
                        )
                        .order_by(MessageTemplate.id.desc())
                    )
                )
                .scalars()
                .first()
            )
            if not tpl:
                return {"greeted": 0, "checked": 0, "skipped": "no_template"}
            template_text = tpl.body

        cutoff = now - timedelta(hours=FRESH_HOURS)
        conditions = [
            AdRequest.status == "new",
            AdRequest.greeting_sent_at.is_(None),
            AdRequest.can_message.is_(True),
            AdRequest.detected_at >= cutoff,
        ]
        if not allow_all:
            conditions.append(AdRequest.community_vk_id.in_(allowlist))
        rows = (
            (await session.execute(select(AdRequest).where(*conditions).limit(MAX_PER_RUN)))
            .scalars()
            .all()
        )
        if not rows:
            return {"greeted": 0, "checked": 0}

        if send is None:
            from modules.notifications.vk_actions import send_message
            from modules.vk_token_router import load_vk_routing

            user_token, community_tokens = await load_vk_routing()
            if not user_token:
                return {"greeted": 0, "checked": len(rows), "skipped": "no_token"}

            def send(group_id: int, peer_id: int, message: str):  # pragma: no cover - сеть
                return send_message(
                    group_id=group_id,
                    peer_id=peer_id,
                    message=message,
                    user_token=user_token,
                    community_tokens=community_tokens,
                    random_id=0,
                )

        greeted = 0
        for ar in rows:
            if ar.author_is_group or not ar.peer_id or int(ar.peer_id) <= 0:
                continue
            text = render(
                template_text,
                author_name=ar.author_name,
                community_name=ar.community_name,
                region_name=ar.community_name,
            )
            try:
                res = send(int(ar.community_vk_id), int(ar.peer_id), text)
            except Exception as e:  # pragma: no cover - защита
                logger.warning("auto-greeting send failed for req %s: %s", ar.id, e)
                continue
            ok = bool(res.get("success")) if isinstance(res, dict) else bool(res)
            if not ok:
                continue
            ar.greeting_sent_at = now
            log_interaction(
                session,
                kind="reply_sent",
                ad_request_id=ar.id,
                client_id=ar.client_id,
                summary="Авто-приветствие рекламодателю",
                actor="system",
            )
            greeted += 1

        await session.commit()

    logger.info("auto-greeting: checked=%d, greeted=%d", len(rows), greeted)
    return {"greeted": greeted, "checked": len(rows)}
