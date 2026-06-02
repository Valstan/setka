"""API рекламного кабинета (`/api/ad-cabinet`).

Инбокс рекламных заявок из предложки + подготовка и полу-авто отправка ответа:

- ``GET  /requests``                  — список заявок (фильтры status/community/дата);
- ``POST /requests/{id}/prepare``     — отрендерить ответ из шаблона (подставить имя/паблик);
- ``POST /requests/{id}/send``        — отправить от сообщества (если VK разрешает ЛС),
                                        иначе вернуть deeplink на личный диалог;
- ``POST /requests/{id}/status``      — сменить статус (contacted/skipped/published).

Отправка — полу-авто: VK почти не даёт сообществу писать первым (error 901),
поэтому ``messages_allowed`` предчекает, а на отказ UI переключается на личный
аккаунт.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdRequest, MessageTemplate
from modules.ad_cabinet.message_builder import render

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_STATUSES = {"new", "contacted", "skipped", "published"}


class PrepareIn(BaseModel):
    template_id: int


class StatusIn(BaseModel):
    status: str


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@router.get("/requests")
async def list_requests(
    status: Optional[str] = None,
    community_vk_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
):
    """Список заявок с серверной фильтрацией, свежие сверху."""
    stmt = select(AdRequest)
    if status:
        stmt = stmt.where(AdRequest.status == status)
    if community_vk_id is not None:
        stmt = stmt.where(AdRequest.community_vk_id == community_vk_id)
    df = _parse_date(date_from) if date_from else None
    dt = _parse_date(date_to) if date_to else None
    if df:
        stmt = stmt.where(AdRequest.detected_at >= df)
    if dt:
        stmt = stmt.where(AdRequest.detected_at <= dt)
    stmt = stmt.order_by(AdRequest.detected_at.desc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"requests": [r.to_dict() for r in rows]}


@router.post("/requests/{request_id}/prepare")
async def prepare_reply(
    request_id: int,
    payload: PrepareIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Отрендерить ответ из шаблона: подставить имя автора и название паблика."""
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    tpl = await db.get(MessageTemplate, payload.template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="template not found")

    text = render(
        tpl.body,
        author_name=ar.author_name,
        community_name=ar.community_name,
        region_name=ar.community_name,
    )
    ar.prepared_message = text
    ar.template_id = tpl.id
    await db.commit()
    return {"prepared_message": text, "template_id": tpl.id}


@router.post("/requests/{request_id}/send")
async def send_reply(request_id: int, db: AsyncSession = Depends(get_db_session)):
    """Полу-авто отправка: от сообщества (если VK разрешает) либо deeplink."""
    from modules.notifications.vk_actions import messages_allowed, send_message
    from modules.vk_token_router import load_vk_routing

    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    if not (ar.prepared_message or "").strip():
        raise HTTPException(status_code=400, detail="Сначала подготовьте сообщение")

    # Идемпотентность: уже отправлено.
    if ar.status == "contacted" and ar.vk_message_id:
        return {"success": True, "already_sent": True, "vk_message_id": ar.vk_message_id}

    # Автор-группа или нерезолвимый peer — ЛС невозможно.
    if ar.author_is_group or not ar.peer_id or int(ar.peer_id) <= 0:
        return {
            "allowed": False,
            "reason": "author_is_group",
            "personal_deeplink": None,
            "prepared_message": ar.prepared_message,
        }

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        return {"success": False, "error": "VK token not found"}

    group_id = int(ar.community_vk_id)
    peer_id = int(ar.peer_id)

    allowed = messages_allowed(
        group_id=group_id,
        user_id=peer_id,
        user_token=user_token,
        community_tokens=community_tokens,
    )
    if allowed is False:
        ar.can_message = False
        ar.can_message_checked_at = datetime.utcnow()
        await db.commit()
        return {
            "allowed": False,
            "personal_deeplink": f"https://vk.com/im?sel={peer_id}",
            "prepared_message": ar.prepared_message,
        }

    # allowed True или None (неизвестно) — пробуем отправить; 901 отработает send_message.
    attachment = ar.message_attachments or _build_offer_attachment(
        group_id, peer_id, community_tokens
    )
    res = send_message(
        group_id=group_id,
        peer_id=peer_id,
        message=ar.prepared_message,
        user_token=user_token,
        community_tokens=community_tokens,
        random_id=ar.id,  # стабильный → идемпотентность на стороне VK
        attachment=attachment or None,
    )

    if res.get("success"):
        ar.status = "contacted"
        ar.contacted_at = datetime.utcnow()
        ar.via = res.get("via")
        ar.vk_message_id = res.get("message_id")
        ar.can_message = True
        ar.can_message_checked_at = datetime.utcnow()
        if attachment and not ar.message_attachments:
            ar.message_attachments = attachment
        await db.commit()
        return {
            "success": True,
            "vk_message_id": res.get("message_id"),
            "via": res.get("via"),
        }

    # Не отправлено. 900/901/902 → фолбэк на личный диалог.
    if res.get("allowed") is False:
        ar.can_message = False
        ar.can_message_checked_at = datetime.utcnow()
        await db.commit()
        return {
            "allowed": False,
            "personal_deeplink": res.get("personal_deeplink"),
            "prepared_message": ar.prepared_message,
            "error_code": res.get("error_code"),
        }

    return {
        "success": False,
        "error": res.get("error"),
        "error_code": res.get("error_code"),
    }


@router.post("/requests/{request_id}/status")
async def set_status(
    request_id: int,
    payload: StatusIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Сменить статус заявки вручную."""
    if payload.status not in _VALID_STATUSES:
        raise HTTPException(status_code=400, detail="invalid status")
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    ar.status = payload.status
    if payload.status == "contacted" and not ar.contacted_at:
        ar.contacted_at = datetime.utcnow()
    await db.commit()
    return ar.to_dict()


# ----------------------------------------------------------------------
# Офферные картинки
# ----------------------------------------------------------------------


def _offer_image_paths() -> List[Path]:
    """Файлы офферных картинок из web/static/ad_offers/ (jpg/png), отсортированы."""
    d = Path(__file__).resolve().parents[1] / "static" / "ad_offers"
    if not d.is_dir():
        return []
    return sorted(
        p for p in d.iterdir() if p.is_file() and p.suffix.lower() in (".jpg", ".jpeg", ".png")
    )


def _build_offer_attachment(group_id: int, peer_id: int, community_tokens) -> str:
    """Залить офферные картинки и вернуть attachment-строку (best-effort).

    Картинки в ЛС нужно слать community-токеном группы (R4); если его нет или
    картинок нет — возвращаем '' (оффер уйдёт текстом).
    """
    paths = _offer_image_paths()
    if not paths:
        return ""
    tok = (community_tokens or {}).get(abs(int(group_id)))
    if not tok:
        return ""
    try:
        import vk_api

        from modules.ad_cabinet.vk_photo_upload import upload_offer_images

        api = vk_api.VkApi(token=tok).get_api()
        images = [p.read_bytes() for p in paths[:5]]
        return upload_offer_images(api, images, peer_id=peer_id)
    except Exception as e:
        logger.warning("offer image upload failed: %s", e)
        return ""
