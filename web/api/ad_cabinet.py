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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import AdClient, AdRequest, AdScheduledPost, MessageTemplate
from modules.ad_cabinet.interaction_log import log_interaction
from modules.ad_cabinet.message_builder import render

logger = logging.getLogger(__name__)
router = APIRouter()

_VALID_STATUSES = {"new", "contacted", "skipped", "published"}
# Единый роутер входящих ЛС (Этап 1): куда направлено сообщение и наш статус обработки.
_VALID_ROUTES = {"ad_cabinet", "notifications"}
_VALID_HANDLING = {"new", "in_progress", "done"}

# Время оператор вводит по Москве; VK ждёт unix (UTC). МСК = UTC+3, без DST.
MSK = timezone(timedelta(hours=3))

# Офферные картинки: что принимаем и сколько максимум весит загружаемый файл.
_ALLOWED_IMG_EXT = (".jpg", ".jpeg", ".png")
_MAX_IMG_BYTES = 12 * 1024 * 1024  # 12 МБ — с запасом под прайс-PNG


class PrepareIn(BaseModel):
    template_id: int


class StatusIn(BaseModel):
    status: str


class RouteIn(BaseModel):
    """Сменить маршрут заявки/сообщения: 'ad_cabinet' | 'notifications' (R3)."""

    route: str


class HandlingIn(BaseModel):
    """Наш статус обработки сообщения: 'new' | 'in_progress' | 'done' (R2)."""

    handling_status: str


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


class ScheduleCreateIn(BaseModel):
    """Создать N отложенных постов: один текст+картинки на каждую из ``dates``.

    ``dates`` — список ISO-datetime по МСК (на каждую — отдельный пост в
    VK-отложке). ``images`` — имена выбранных офферных картинок (заливаются на
    стену один раз и переиспользуются для всех дат). Тумблеры
    ``from_group``/``signed``/``comments_enabled`` применяются ко всем постам.
    """

    community_vk_id: int
    region_id: Optional[int] = None
    text: str = ""
    images: Optional[List[str]] = None
    dates: List[str]
    from_group: bool = True
    signed: bool = False
    comments_enabled: bool = True
    source_ad_request_id: Optional[int] = None
    # Блок B2: запланировать заявку из предложки «пересозданием». VK API не даёт
    # править предложенный пост in-place (wall.edit → 15/27), поэтому планируем
    # новый пост (wall.post publish_date), а оригинал убираем из предложки
    # (wall.delete) и помечаем заявку опубликованной. Только при
    # ``source_ad_request_id`` и хотя бы одной успешно запланированной дате.
    remove_original: bool = False
    # Блок C (CRM): привязка отложенного поста к клиенту-рекламодателю и цена
    # размещения. ``client_id`` оператор передаёт явно (из заявки), либо бэкенд
    # сам резолвит из ``source_ad_request_id``. При успешной раскладке клиент
    # продвигается в стадию ``scheduled`` (мягко — не понижает paid/published).
    client_id: Optional[int] = None
    price: Optional[float] = None
    # Срок размещения (С2): авто-снятие поста. Опционально (решение владельца —
    # нет срока → висит вечно). Два способа на выбор оператора:
    #   expire_days — снять через N дней после публикации каждого поста;
    #   expire_at   — явная дата снятия (ISO, МСК) для всех постов раскладки.
    # Если заданы оба — приоритет у expire_at. Ни одного → expires_at=NULL.
    expire_days: Optional[int] = None
    expire_at: Optional[str] = None


class AcceptRequestIn(BaseModel):
    """Сквозное оформление заявки одной кнопкой (С5): клиент + отложка + ответ.

    Композиция движков С1–С4: завести/привязать клиента → опц. ответить →
    запланировать пост (цена/срок, убрать оригинал, пометить заявку
    опубликованной). Поля планирования — как в ``ScheduleCreateIn``.
    """

    dates: List[str]
    price: Optional[float] = None
    expire_days: Optional[int] = None
    expire_at: Optional[str] = None
    from_group: bool = True
    signed: bool = False
    comments_enabled: bool = True
    remove_original: bool = True
    reply_message: Optional[str] = None  # если задано — отправить ответ клиенту


def _parse_date(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


@router.get("/requests")
async def list_requests(
    status: Optional[str] = None,
    origin: Optional[str] = None,
    route: Optional[str] = None,
    community_vk_id: Optional[int] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
):
    """Список заявок с серверной фильтрацией, свежие сверху.

    ``origin`` — источник заявки (``suggested`` / ``inbound_dm``); без него —
    все источники. ``route`` — где «живёт» сообщение (``ad_cabinet`` /
    ``notifications``); инбокс кабинета запрашивает ``route='ad_cabinet'``, чтобы
    не-рекламные ЛС, отданные в уведомления, сюда не попадали.
    """
    stmt = select(AdRequest)
    if status:
        stmt = stmt.where(AdRequest.status == status)
    if origin:
        stmt = stmt.where(AdRequest.origin == origin)
    if route:
        stmt = stmt.where(AdRequest.route == route)
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
        log_interaction(
            db,
            kind="reply_sent",
            client_id=ar.client_id,
            ad_request_id=ar.id,
            summary="Отправлен ответ клиенту: " + (ar.prepared_message or "")[:120],
            meta={"via": res.get("via")},
        )
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
    log_interaction(
        db,
        kind="status_changed",
        client_id=ar.client_id,
        ad_request_id=ar.id,
        summary=f"Статус заявки → {payload.status}",
        meta={"status": payload.status},
    )
    await db.commit()
    return ar.to_dict()


@router.post("/requests/{request_id}/route")
async def set_route(
    request_id: int,
    payload: RouteIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Перенести сообщение между кабинетом и уведомлениями (R3).

    «Не реклама → в уведомления» (из `/ad-cabinet`) и «Это реклама → в кабинет»
    (из `/notifications`) дёргают этот эндпоинт. Переезд сбрасывает наш статус
    обработки в ``new`` — на новом месте сообщение всплывает как необработанное.
    """
    if payload.route not in _VALID_ROUTES:
        raise HTTPException(status_code=400, detail="invalid route")
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    ar.route = payload.route
    ar.handling_status = "new"
    ar.handled_at = None
    log_interaction(
        db,
        kind="status_changed",
        client_id=ar.client_id,
        ad_request_id=ar.id,
        summary=f"Маршрут → {payload.route}",
        meta={"route": payload.route},
    )
    await db.commit()
    return ar.to_dict()


@router.post("/requests/{request_id}/handling")
async def set_handling(
    request_id: int,
    payload: HandlingIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Сменить НАШ статус обработки сообщения (R2): new|in_progress|done.

    Источник истины «обработано» — этот флаг, а не VK read/unread. ``done`` ставит
    ``handled_at`` (для архива); любой другой статус его снимает (реоткрытие).
    """
    if payload.handling_status not in _VALID_HANDLING:
        raise HTTPException(status_code=400, detail="invalid handling_status")
    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    ar.handling_status = payload.handling_status
    ar.handled_at = datetime.utcnow() if payload.handling_status == "done" else None
    await db.commit()
    return ar.to_dict()


@router.get("/requests/{request_id}/thread")
async def get_thread(
    request_id: int,
    count: int = 30,
    db: AsyncSession = Depends(get_db_session),
):
    """История переписки для заявки из входящих ЛС (блок A, тред-вью).

    Только для ``origin='inbound_dm'`` — у предложки переписки нет. Возвращает
    сообщения от старых к новым через ``messages.getHistory`` (community-токен
    приоритетно, user-токен fallback). Никогда не 500-ит на флапе VK — пустой
    список и ``reason``.
    """
    import asyncio

    from modules.notifications.vk_dialogs_checker import VKDialogsChecker
    from modules.vk_token_router import load_vk_routing

    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    if ar.origin != "inbound_dm" or not ar.peer_id:
        return {"messages": [], "reason": "not_dm"}

    user_token, community_tokens = await load_vk_routing()
    if not user_token:
        return {"messages": [], "reason": "no_token"}

    try:
        checker = VKDialogsChecker(user_token, community_tokens=community_tokens)
        messages = await asyncio.to_thread(
            checker.fetch_history, int(ar.community_vk_id), int(ar.peer_id), count
        )
    except Exception as e:  # pragma: no cover - защита от неожиданного
        logger.warning("thread fetch failed for request %s: %s", request_id, e)
        return {"messages": [], "reason": "error", "error": str(e)}
    return {"messages": messages}


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


# ----------------------------------------------------------------------
# Планировщик отложенных постов (B1)
# ----------------------------------------------------------------------

_VALID_SCHEDULED_STATUSES = {"draft", "scheduled", "published", "failed", "cancelled"}


def _msk_to_unix(naive_msk: datetime) -> int:
    """Naive datetime, трактуемый как МСК wall-clock → unix-секунды (UTC)."""
    return int(naive_msk.replace(tzinfo=MSK).timestamp())


def _build_wall_attachment(
    group_id: int,
    community_tokens,
    filenames: Optional[List[str]] = None,
) -> List[str]:
    """Залить выбранные офферные картинки на стену группы (best-effort).

    Возвращает список attachment-строк ``["photo<o>_<id>", …]`` (пустой, если
    картинок нет или у группы нет community-токена — тогда пост уйдёт текстом).
    Грузить надо community-токеном целевой группы (владелец фото = группа).
    """
    paths = _offer_image_paths()
    if filenames is not None:
        wanted = {Path(f).name for f in filenames}
        paths = [p for p in paths if p.name in wanted]
    if not paths:
        return []
    tok = (community_tokens or {}).get(abs(int(group_id)))
    if not tok:
        return []
    try:
        import vk_api

        from modules.publisher.vk_wall_photo_upload import upload_wall_images

        api = vk_api.VkApi(token=tok).get_api()
        images = [p.read_bytes() for p in paths[:10]]
        return upload_wall_images(api, images, group_id=group_id)
    except Exception as e:
        logger.warning("wall image upload failed: %s", e)
        return []


@router.post("/scheduled")
async def create_scheduled(
    payload: ScheduleCreateIn,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать отложенные посты по выбранным датам (VK-«Отложенные записи»).

    На каждую дату — отдельный ``wall.post(publish_date=…)`` целевым community-
    токеном. Картинки заливаются на стену один раз и переиспользуются. Частичный
    успех допустим: каждая строка несёт свой ``status`` (scheduled|failed).
    """
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_token_router import load_vk_routing

    if not payload.dates:
        raise HTTPException(status_code=400, detail="Нужна хотя бы одна дата публикации")
    if not (payload.text or "").strip() and not payload.images:
        raise HTTPException(status_code=400, detail="Пустой пост: нужен текст или картинки")

    # Парсим даты (МСК) → (naive_dt, unix). Всё должно быть в будущем (VK требует).
    now_unix = int(datetime.now(tz=MSK).timestamp())
    parsed: List[tuple] = []
    for s in payload.dates:
        dt = _parse_date(s)
        if dt is None:
            raise HTTPException(status_code=400, detail=f"Некорректная дата: {s}")
        dt = dt.replace(tzinfo=None)
        unix = _msk_to_unix(dt)
        if unix <= now_unix + 60:
            raise HTTPException(status_code=400, detail=f"Дата в прошлом или слишком близко: {s}")
        parsed.append((dt, unix))

    # Срок размещения (С2): опционален. expire_at (явная дата) важнее expire_days.
    expire_at_dt: Optional[datetime] = None
    if payload.expire_at:
        expire_at_dt = _parse_date(payload.expire_at)
        if expire_at_dt is None:
            raise HTTPException(
                status_code=400, detail=f"Некорректная дата снятия: {payload.expire_at}"
            )
        expire_at_dt = expire_at_dt.replace(tzinfo=None)
    if payload.expire_days is not None and payload.expire_days <= 0:
        raise HTTPException(status_code=400, detail="Срок в днях должен быть положительным")

    gid = int(payload.community_vk_id)
    _user_token, community_tokens = await load_vk_routing()

    # Картинки на стену — один раз, переиспользуем attachment'ы для всех дат.
    attachment_list = _build_wall_attachment(gid, community_tokens, payload.images)
    attachments_str = ",".join(attachment_list) if attachment_list else None

    try:
        publisher = await VKPublisher.create_with_policy(db, target_group_id=gid)
    except Exception as e:
        logger.exception("scheduler: failed to build publisher")
        raise HTTPException(status_code=500, detail=f"Нет токена для публикации: {e}")

    created: List[AdScheduledPost] = []
    for naive_dt, unix in parsed:
        # Срок снятия (С2): явная дата (для всех) либо +N дней от публикации поста.
        if expire_at_dt is not None:
            row_expires = expire_at_dt
        elif payload.expire_days:
            row_expires = naive_dt + timedelta(days=payload.expire_days)
        else:
            row_expires = None
        row = AdScheduledPost(
            community_vk_id=gid,
            region_id=payload.region_id,
            text=payload.text or "",
            image_names=payload.images or [],
            attachments=attachments_str,
            publish_date=naive_dt,
            expires_at=row_expires,
            from_group=payload.from_group,
            signed=payload.signed,
            comments_enabled=payload.comments_enabled,
            source_ad_request_id=payload.source_ad_request_id,
            client_id=payload.client_id,
            price=payload.price,
            status="draft",
        )
        db.add(row)
        try:
            res = await publisher.publish_digest(
                group_id=gid,
                text=payload.text or "",
                attachments=attachment_list or None,
                from_group=payload.from_group,
                publish_date=unix,
                signed=payload.signed,
            )
            if res.get("success"):
                row.status = "scheduled"
                row.vk_postponed_post_id = res.get("post_id")
                # VK по умолчанию оставляет комментарии включёнными — закрываем
                # только если оператор явно выключил.
                if not payload.comments_enabled and row.vk_postponed_post_id:
                    await publisher.set_post_comments(
                        gid, int(row.vk_postponed_post_id), enabled=False
                    )
            else:
                row.status = "failed"
                row.error_message = str(res.get("error"))[:500]
        except Exception as e:
            logger.exception("scheduler: publish failed for %s @ %s", gid, naive_dt)
            row.status = "failed"
            row.error_message = str(e)[:500]
        created.append(row)

    await db.commit()
    for r in created:
        await db.refresh(r)
    scheduled_n = sum(1 for r in created if r.status == "scheduled")

    # Блок C: привязать отложку к CRM-клиенту и продвинуть воронку в "scheduled".
    # Клиента берём явный (payload.client_id) либо резолвим из исходной заявки.
    # Делаем только при реально запланированных датах — пустую раскладку не
    # считаем сделкой и лишний раз VK/БД не трогаем.
    effective_client_id = payload.client_id
    if effective_client_id is None and payload.source_ad_request_id and scheduled_n > 0:
        src_ar = await db.get(AdRequest, int(payload.source_ad_request_id))
        if src_ar and src_ar.client_id:
            effective_client_id = src_ar.client_id
            # Бэкфилл client_id на успешно запланированные строки (явного не было).
            for r in created:
                if r.status == "scheduled" and r.client_id is None:
                    r.client_id = effective_client_id
    if effective_client_id and scheduled_n > 0:
        client = await db.get(AdClient, int(effective_client_id))
        if client and client.stage in ("detected", "contacted"):
            client.stage = "scheduled"
        await db.commit()

    # Журнал: фиксируем планирование в таймлайн (по клиенту и/или заявке).
    if scheduled_n > 0 and (effective_client_id or payload.source_ad_request_id):
        first_scheduled = next((r for r in created if r.status == "scheduled"), None)
        log_interaction(
            db,
            kind="scheduled",
            client_id=effective_client_id,
            ad_request_id=payload.source_ad_request_id,
            scheduled_post_id=first_scheduled.id if first_scheduled else None,
            summary=f"Запланировано постов: {scheduled_n} (сообщество {gid})",
            meta={"scheduled": scheduled_n, "community_vk_id": gid},
        )
        await db.commit()

    # Блок B2: заявка из предложки запланирована пересозданием — убираем оригинал
    # из предложки (wall.delete, проходит через user-token fallback на коде 27) и
    # помечаем заявку опубликованной. Только если что-то реально запланировано —
    # иначе не теряем заявку при полном провале планирования.
    original_removed = False
    original_remove_error: Optional[str] = None
    if payload.remove_original and payload.source_ad_request_id and scheduled_n > 0:
        ar = await db.get(AdRequest, int(payload.source_ad_request_id))
        if ar and ar.community_vk_id and ar.vk_post_id:
            res = await publisher.delete_post(int(ar.community_vk_id), int(ar.vk_post_id))
            if res.get("success"):
                original_removed = True
            else:
                original_remove_error = str(res.get("error"))[:300]
        if ar:
            # Заявка обработана: статус published независимо от удаления оригинала
            # (пост уже запланирован; оригинал оператор при сбое уберёт вручную).
            ar.status = "published"
            if not ar.contacted_at:
                ar.contacted_at = datetime.utcnow()
            await db.commit()

    return {
        "created": [r.to_dict() for r in created],
        "scheduled": scheduled_n,
        "failed": len(created) - scheduled_n,
        "original_removed": original_removed,
        "original_remove_error": original_remove_error,
        "client_id": effective_client_id,
    }


@router.post("/requests/{request_id}/accept")
async def accept_request(
    request_id: int,
    payload: AcceptRequestIn,
    db: AsyncSession = Depends(get_db_session),
):
    """С5 — сквозное оформление заявки одной кнопкой.

    Композиция С1–С4 в один ход: (1) завести/привязать клиента из заявки;
    (2) опц. ответить клиенту; (3) запланировать пост движком ``create_scheduled``
    (резолв клиента, цена/срок, убрать оригинал, пометить заявку published,
    событие в таймлайн). Возвращает свод результатов.
    """
    from web.api.ad_crm import upsert_from_request

    ar = await db.get(AdRequest, request_id)
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    # Идемпотентность: повторный «Оформить» по уже опубликованной заявке не должен
    # повторно слать ответ-ЛС и плодить вторую отложку. (Был пропущен guard.)
    if ar.status == "published":
        return {"already": True, "scheduled": 0, "failed": 0, "client_id": ar.client_id}
    if not payload.dates:
        raise HTTPException(status_code=400, detail="Нужна хотя бы одна дата публикации")

    # 1) Клиент из заявки — проставит ar.client_id (создаст или привяжет).
    client_res = await upsert_from_request(request_id, db)

    # 2) Ответ клиенту (опц.) — до пометки published; send_reply идемпотентен.
    reply_res = None
    if payload.reply_message and payload.reply_message.strip():
        reply_res = await send_reply(request_id, SendIn(message=payload.reply_message), db=db)

    # 3) Планирование движком create_scheduled (он же уберёт оригинал и пометит
    #    заявку published). client_id резолвится из ar.source_ad_request_id.
    sched_res = await create_scheduled(
        ScheduleCreateIn(
            community_vk_id=int(ar.community_vk_id),
            region_id=ar.region_id,
            text=ar.text_snapshot or "",
            dates=payload.dates,
            price=payload.price,
            expire_days=payload.expire_days,
            expire_at=payload.expire_at,
            from_group=payload.from_group,
            signed=payload.signed,
            comments_enabled=payload.comments_enabled,
            source_ad_request_id=request_id,
            remove_original=payload.remove_original,
        ),
        db=db,
    )

    return {
        "client": client_res,
        "reply": reply_res,
        "scheduled": sched_res.get("scheduled", 0),
        "failed": sched_res.get("failed", 0),
        "original_removed": sched_res.get("original_removed", False),
        "client_id": sched_res.get("client_id"),
    }


def _upload_request_photos(group_id: int, community_tokens, photo_urls) -> List[str]:
    """Скачать фото заявки по CDN-URL и залить на стену группы → attachment-строки.

    Best-effort: нет community-токена / фото / сети → пустой список (пост уйдёт
    текстом). Грузим токеном целевой группы (владелец фото = группа)."""
    urls = [u for u in (photo_urls or []) if isinstance(u, str) and u.startswith("http")]
    if not urls:
        return []
    tok = (community_tokens or {}).get(abs(int(group_id)))
    if not tok:
        return []
    try:
        import httpx
        import vk_api

        from modules.publisher.vk_wall_photo_upload import upload_wall_images

        images: List[bytes] = []
        for u in urls[:10]:
            try:
                r = httpx.get(u, timeout=20)
                if r.status_code == 200 and r.content:
                    images.append(r.content)
            except Exception:  # noqa: BLE001 - одно битое фото не валит остальные
                continue
        if not images:
            return []
        api = vk_api.VkApi(token=tok).get_api()
        return upload_wall_images(api, images, group_id=group_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("publish-now image upload failed: %s", e)
        return []


@router.post("/requests/{request_id}/publish")
async def publish_request_now(
    request_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Опубликовать заявку из предложки СЕЙЧАС и бесплатно — для бытовых объявлений.

    Когда с заявки дохода нет (продам огурцы, отдам котят): моментальный ``wall.post``
    контента заявки (текст + её фото) в сообщество, снятие оригинала из предложки,
    пометка ``published`` → карточка уходит из «Входящих». Отличие от ``/accept``
    (платный пайплайн: клиент+цена+отложка) и ``/status`` (только смена статуса, без
    реальной публикации). Идемпотентность: статус коммитим СРАЗУ после успешного
    поста (защита от повторной публикации — иначе дубль на стене)."""
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.vk_token_router import load_vk_routing

    # FOR UPDATE — атомарная защита от двойного клика: конкурентный второй запрос
    # блокируется на строке до коммита первого, затем видит status='published' и
    # выходит «already» (иначе оба прочли бы 'new' до коммита и запостили дважды
    # в живую стену — async-интерливинг на await publish_digest).
    ar = (
        await db.execute(select(AdRequest).where(AdRequest.id == request_id).with_for_update())
    ).scalar_one_or_none()
    if not ar:
        raise HTTPException(status_code=404, detail="ad request not found")
    if not ar.community_vk_id:
        raise HTTPException(status_code=400, detail="у заявки нет сообщества для публикации")
    if ar.status == "published":
        return {"published": True, "already": True}
    text = (ar.text_snapshot or "").strip()
    photo_urls = ar.photo_urls_json or []
    if not text and not photo_urls:
        raise HTTPException(status_code=400, detail="Пустая заявка: нет ни текста, ни фото")

    gid = int(ar.community_vk_id)
    _user_token, community_tokens = await load_vk_routing()
    attachments = _upload_request_photos(gid, community_tokens, photo_urls)

    publisher = await VKPublisher.create_with_policy(db, target_group_id=gid)
    res = await publisher.publish_digest(group_id=gid, text=text, attachments=attachments)
    if not res.get("success"):
        raise HTTPException(status_code=502, detail=f"Публикация не удалась: {res.get('error')}")

    # Пометить published СРАЗУ (отдельный commit) — защита от повторной публикации.
    ar.status = "published"
    if not ar.contacted_at:
        ar.contacted_at = datetime.utcnow()
    await db.commit()

    # Best-effort: снять оригинал из предложки + записать в таймлайн.
    original_removed = False
    if ar.vk_post_id:
        rm = await publisher.delete_post(gid, int(ar.vk_post_id))
        original_removed = bool(rm.get("success"))
    try:
        log_interaction(
            db,
            kind="published",
            ad_request_id=ar.id,
            summary=f"Опубликовано бесплатно в сообщество {gid}",
            meta={"community_vk_id": gid, "free": True, "url": res.get("url")},
        )
        await db.commit()
    except Exception as e:  # noqa: BLE001 - лог не критичен, пост уже опубликован
        logger.warning("publish-now log_interaction failed: %s", e)

    return {
        "published": True,
        "url": res.get("url"),
        "original_removed": original_removed,
        "attachments": len(attachments),
    }


@router.get("/scheduled")
async def list_scheduled(
    community_vk_id: Optional[int] = None,
    status: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    limit: int = 200,
    db: AsyncSession = Depends(get_db_session),
):
    """Запланированные посты (календарь): ближайшие сверху."""
    stmt = select(AdScheduledPost)
    if community_vk_id is not None:
        stmt = stmt.where(AdScheduledPost.community_vk_id == community_vk_id)
    if status:
        stmt = stmt.where(AdScheduledPost.status == status)
    df = _parse_date(date_from) if date_from else None
    dt = _parse_date(date_to) if date_to else None
    if df:
        stmt = stmt.where(AdScheduledPost.publish_date >= df)
    if dt:
        stmt = stmt.where(AdScheduledPost.publish_date <= dt)
    stmt = stmt.order_by(AdScheduledPost.publish_date.asc()).limit(limit)
    rows = (await db.execute(stmt)).scalars().all()
    return {"scheduled": [r.to_dict() for r in rows]}


@router.post("/scheduled/{post_id}/cancel")
async def cancel_scheduled(
    post_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    """Отменить запланированный пост: убрать из VK-отложки (wall.delete) + статус.

    Если VK-удаление не удалось (пост уже опубликован/недоступен) — статус НЕ
    меняем и возвращаем ``cancel_error``, чтобы оператор видел реальное состояние.
    """
    from modules.publisher.vk_publisher_extended import VKPublisher

    row = await db.get(AdScheduledPost, post_id)
    if not row:
        raise HTTPException(status_code=404, detail="scheduled post not found")
    if row.status == "cancelled":
        return row.to_dict()

    if row.vk_postponed_post_id:
        publisher = await VKPublisher.create_with_policy(
            db, target_group_id=int(row.community_vk_id)
        )
        res = await publisher.delete_post(int(row.community_vk_id), int(row.vk_postponed_post_id))
        if not res.get("success"):
            return {**row.to_dict(), "cancel_error": res.get("error")}

    log_interaction(
        db,
        kind="cancelled",
        client_id=row.client_id,
        scheduled_post_id=row.id,
        summary="Отменён запланированный пост",
    )
    row.status = "cancelled"
    await db.commit()
    await db.refresh(row)
    return row.to_dict()
