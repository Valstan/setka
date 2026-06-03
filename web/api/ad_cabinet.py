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
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdRequest, MessageTemplate
from modules.ad_cabinet.message_builder import render

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_STATUSES = {"new", "contacted", "skipped", "published"}

# Офферные картинки: что принимаем и сколько максимум весит загружаемый файл.
_ALLOWED_IMG_EXT = (".jpg", ".jpeg", ".png")
_MAX_IMG_BYTES = 12 * 1024 * 1024  # 12 МБ — с запасом под прайс-PNG


class PrepareIn(BaseModel):
    template_id: int


class StatusIn(BaseModel):
    status: str


class BulkActionIn(BaseModel):
    """Массовое действие над заявками инбокса.

    ``action='status'`` — сменить статус всем ``ids`` (нужен ``status``);
    ``action='delete'`` — удалить заявки.
    """

    ids: List[int]
    action: str
    status: Optional[str] = None


class SendIn(BaseModel):
    """Тело запроса отправки: правки оператора + выбранные картинки.

    ``message`` — отредактированный текст ответа (приоритет над сохранённым
    ``prepared_message``); ``images`` — имена выбранных офферных картинок
    (``None`` = поведение по умолчанию/кэш; ``[]`` = без картинок).
    """

    message: Optional[str] = None
    images: Optional[List[str]] = None


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
async def send_reply(
    request_id: int,
    payload: Optional[SendIn] = None,
    db: AsyncSession = Depends(get_db_session),
):
    """Полу-авто отправка: от сообщества (если VK разрешает) либо deeplink.

    Тело письма — правки оператора из ``payload.message`` (если переданы),
    вложения — выбранные офферные картинки из ``payload.images``.
    """
    from modules.notifications.vk_actions import messages_allowed, send_message
    from modules.vk_token_router import load_vk_routing

    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")

    # Идемпотентность: уже отправлено — не трогаем текст, не шлём повторно.
    if ar.status == "contacted" and ar.vk_message_id:
        return {"success": True, "already_sent": True, "vk_message_id": ar.vk_message_id}

    # Правки оператора имеют приоритет над сохранённым prepared_message.
    if payload and payload.message is not None and payload.message.strip():
        ar.prepared_message = payload.message.strip()
    if not (ar.prepared_message or "").strip():
        raise HTTPException(status_code=400, detail="Сначала подготовьте сообщение")

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

    # Свежий precheck со скана (modules.ad_cabinet.scanner) — не дёргаем VK
    # повторно. Messageability меняется редко; окно 7 дней с запасом.
    if (
        ar.can_message is not None
        and ar.can_message_checked_at
        and (datetime.utcnow() - ar.can_message_checked_at) < timedelta(days=7)
    ):
        allowed = ar.can_message
    else:
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
    # Картинки: если оператор передал выбор — грузим именно его (пустой список =
    # без картинок); если выбор не передан — используем кэш или все офферные (legacy).
    selected = payload.images if payload else None
    if selected is not None:
        attachment = _build_offer_attachment(
            group_id, peer_id, community_tokens, filenames=selected
        )
    else:
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


@router.post("/requests/bulk-action")
async def bulk_action(
    payload: BulkActionIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Массовое действие над выбранными заявками: смена статуса или удаление.

    Один batch-запрос вместо построчных вызовов из UI. Возвращает число
    затронутых строк.
    """
    ids = [int(i) for i in (payload.ids or [])]
    if not ids:
        raise HTTPException(status_code=400, detail="no ids")

    if payload.action == "delete":
        result = await db.execute(delete(AdRequest).where(AdRequest.id.in_(ids)))
        await db.commit()
        return {"action": "delete", "affected": int(result.rowcount or 0)}

    if payload.action == "status":
        if payload.status not in _VALID_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status")
        result = await db.execute(
            update(AdRequest).where(AdRequest.id.in_(ids)).values(status=payload.status)
        )
        # Проставляем contacted_at тем, у кого его ещё нет (для отметки «связались»).
        if payload.status == "contacted":
            await db.execute(
                update(AdRequest)
                .where(AdRequest.id.in_(ids), AdRequest.contacted_at.is_(None))
                .values(contacted_at=datetime.utcnow())
            )
        await db.commit()
        return {"action": "status", "status": payload.status, "affected": int(result.rowcount or 0)}

    raise HTTPException(status_code=400, detail="invalid action")


# ----------------------------------------------------------------------
# Офферные картинки
# ----------------------------------------------------------------------


def _offer_dir() -> Path:
    """Каталог офферных картинок (создаётся при первом обращении)."""
    d = Path(__file__).resolve().parents[1] / "static" / "ad_offers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _offer_image_paths() -> List[Path]:
    """Файлы офферных картинок (jpg/png), отсортированы по имени."""
    d = _offer_dir()
    return sorted(p for p in d.iterdir() if p.is_file() and p.suffix.lower() in _ALLOWED_IMG_EXT)


def _safe_offer_name(name: str) -> str:
    """Базовое имя без путей. Отсекает path-traversal и скрытые файлы."""
    base = Path(str(name or "")).name.strip()
    if not base or base.startswith("."):
        raise HTTPException(status_code=400, detail="Некорректное имя файла")
    return base


def _offer_image_dto(p: Path) -> dict:
    return {
        "name": p.name,
        "url": "/static/ad_offers/" + quote(p.name),
        "size": p.stat().st_size,
    }


def _build_offer_attachment(
    group_id: int,
    peer_id: int,
    community_tokens,
    filenames: Optional[List[str]] = None,
) -> str:
    """Залить офферные картинки и вернуть attachment-строку (best-effort).

    ``filenames`` — какие именно картинки слать (имена файлов). ``None`` =
    все офферные (legacy). Картинки в ЛС нужно слать community-токеном группы
    (R4); если его нет или картинок нет — возвращаем '' (оффер уйдёт текстом).
    """
    paths = _offer_image_paths()
    if filenames is not None:
        wanted = {Path(f).name for f in filenames}
        paths = [p for p in paths if p.name in wanted]
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


# ----------------------------------------------------------------------
# Библиотека офферных картинок (CRUD): список / загрузка / удаление
# ----------------------------------------------------------------------


@router.get("/offer-images")
async def list_offer_images():
    """Список офферных картинок с превью-URL (для галереи в /ad-cabinet)."""
    return {"images": [_offer_image_dto(p) for p in _offer_image_paths()]}


@router.post("/offer-images")
async def upload_offer_image(file: UploadFile = File(...)):
    """Загрузить картинку в библиотеку офферов (JPG/PNG, до 12 МБ)."""
    name = _safe_offer_name(file.filename or "offer.png")
    if Path(name).suffix.lower() not in _ALLOWED_IMG_EXT:
        raise HTTPException(status_code=400, detail="Только JPG или PNG")
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Пустой файл")
    if len(data) > _MAX_IMG_BYTES:
        raise HTTPException(
            status_code=400, detail=f"Файл больше {_MAX_IMG_BYTES // (1024 * 1024)} МБ"
        )
    dest = _offer_dir() / name
    dest.write_bytes(data)
    return _offer_image_dto(dest)


@router.delete("/offer-images/{name}")
async def delete_offer_image(name: str):
    """Удалить картинку из библиотеки офферов."""
    p = _offer_dir() / _safe_offer_name(name)
    if not p.is_file():
        raise HTTPException(status_code=404, detail="Файл не найден")
    p.unlink()
    return {"success": True}
