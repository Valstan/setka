"""
VK Tokens Management API
API для управления токенами VK через веб-интерфейс
"""

import logging
from datetime import datetime
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import VKToken
from modules.vk_monitor.vk_client import VKClient

router = APIRouter()
logger = logging.getLogger(__name__)


def compute_token_stats(tokens: List[Dict[str, Any]]) -> Dict[str, int]:
    """Категоризация токенов для плашек-счётчиков на ``/tokens``.

    Чистая функция (без БД/IO) — единый источник истины для UI, тестируема.

    ``tokens`` — список dict'ов с ключами ``community_id`` (int | None) и
    ``validation_status`` (str | None).

    - ``main``   — валидные **user**-токены (``community_id is None``): костяк
      парсинга/публикации (VALSTAN, VITA…).
    - ``aux``    — валидные **community**-токены (``community_id`` задан):
      публикуют в свою группу (``COMM_*``).
    - ``broken`` — любые невалидные (``validation_status != 'valid'``,
      включая ``unknown``/cooldown-протухшие) — user и community вместе.
    - ``total``  — всего.

    Разбиение чистое: ``main + aux + broken == total``. Это чинит прежнюю
    заглушку (``main=aux=0`` с устаревшим комментарием «type is not stored in
    DB» — на деле ``community_id`` есть в модели с миграции 007).
    """
    main = aux = broken = 0
    for t in tokens:
        is_valid = (t.get("validation_status") or "unknown") == "valid"
        is_community = t.get("community_id") is not None
        if not is_valid:
            broken += 1
        elif is_community:
            aux += 1
        else:
            main += 1
    return {"main": main, "aux": aux, "broken": broken, "total": len(tokens)}


class TokenResponse(BaseModel):
    """Ответ с информацией о токене"""

    id: int
    name: str
    token: str  # Маскированный токен
    community_id: int | None = None  # abs(vk_group_id) если это community-токен
    is_active: bool
    last_used: str | None
    last_validated: str | None
    validation_status: str
    error_message: str | None
    permissions: List[str] | None  # Изменено на список
    user_info: Dict[str, Any] | None
    # TokenPolicy state (миграция 014)
    disabled_until: str | None = None
    last_error_code: int | None = None
    last_error_at: str | None = None
    consecutive_errors: int = 0
    role: str | None = None  # 'publish' = разрешено публиковать (миграция 023)
    created_at: str | None
    updated_at: str | None


class TokenDisableRequest(BaseModel):
    """POST /api/tokens/{name}/disable body."""

    hours: float = Field(
        24.0,
        gt=0,
        le=24 * 30,
        description="На сколько часов отключить (макс 30 дней).",
    )
    reason: str = Field(
        "manual",
        max_length=200,
        description="Произвольная причина для аудита (попадёт в error_message).",
    )


class TokenUpdateRequest(BaseModel):
    """Запрос на обновление токена"""

    token: str | None = Field(None, min_length=10, description="VK API токен (опционально)")
    validate_token: bool = Field(True, description="Валидировать токен после обновления")
    is_active: bool | None = Field(None, description="Включить/выключить токен (опционально)")


class TokenCreateRequest(BaseModel):
    """Запрос на создание токена"""

    name: str = Field(..., min_length=2, max_length=50, description="Имя токена (например VALSTAN)")
    token: str = Field(..., min_length=10, description="VK API токен")
    validate_token: bool = Field(True, description="Валидировать токен после создания")
    community_id: int | None = Field(
        None,
        description="abs(vk_group_id) если токен community access token; None для user-токена",
    )


class TokenValidationResponse(BaseModel):
    """Ответ валидации токена"""

    name: str
    is_valid: bool
    validation_status: str
    error_message: str | None
    user_info: Dict[str, Any] | None
    permissions: List[str] | None


@router.get("/", response_model=List[TokenResponse])
async def get_all_tokens(db: AsyncSession = Depends(get_db_session)):
    """Получить все токены"""
    try:
        result = await db.execute(select(VKToken).order_by(VKToken.name))
        tokens = result.scalars().all()

        return [TokenResponse(**token.to_dict()) for token in tokens]

    except Exception as e:
        logger.error(f"Error getting tokens: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_token_stats(db: AsyncSession = Depends(get_db_session)):
    """Счётчики для плашек /tokens: main / aux / broken / total.

    Регистрируется ДО ``/{token_name}`` — иначе FastAPI поймает ``/stats``
    как ``token_name="stats"`` (та же причина, что у ``/communities`` ниже).
    """
    try:
        result = await db.execute(select(VKToken))
        tokens = result.scalars().all()
        return compute_token_stats(
            [
                {"community_id": t.community_id, "validation_status": t.validation_status}
                for t in tokens
            ]
        )
    except Exception as e:
        logger.error(f"Error computing token stats: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ---------------------------------------------------------------------------
# Community access tokens: один токен на сообщество для messages.getConversations.
# UI на /tokens рисует таблицу регионов и позволяет вставить токен для каждой.
# Эти роуты должны быть зарегистрированы РАНЬШЕ `/{token_name}`-обработчиков —
# иначе FastAPI поймает `/communities` как `token_name="communities"`.
# ---------------------------------------------------------------------------


class CommunityTokenRow(BaseModel):
    region_id: int
    region_name: str
    region_code: str
    vk_group_id: int
    community_id: int  # abs(vk_group_id)
    token_id: int | None = None
    token_name: str | None = None
    token_masked: str | None = None
    is_active: bool = False
    validation_status: str = "missing"  # missing, valid, invalid, unknown
    last_validated: str | None = None
    error_message: str | None = None
    permissions: List[str] | None = None


class CommunityTokenUpsert(BaseModel):
    token: str = Field(
        ...,
        min_length=10,
        description="Community access token, выпущенный в Управление → Работа с API",
    )
    validate_token: bool = True


@router.get("/communities", response_model=List[CommunityTokenRow])
async def list_community_tokens(db: AsyncSession = Depends(get_db_session)):
    """Per-region список с состоянием community-токена для каждого региона."""
    from database.models import Region

    regions_q = await db.execute(
        select(Region).where(Region.vk_group_id.isnot(None)).order_by(Region.name)
    )
    regions = list(regions_q.scalars())

    tokens_q = await db.execute(select(VKToken).where(VKToken.community_id.isnot(None)))
    tokens_by_cid: Dict[int, VKToken] = {t.community_id: t for t in tokens_q.scalars()}

    rows: List[CommunityTokenRow] = []
    for r in regions:
        cid = abs(int(r.vk_group_id))
        t = tokens_by_cid.get(cid)
        if t:
            rows.append(
                CommunityTokenRow(
                    region_id=r.id,
                    region_name=r.name,
                    region_code=r.code,
                    vk_group_id=r.vk_group_id,
                    community_id=cid,
                    token_id=t.id,
                    token_name=t.name,
                    token_masked=(
                        (t.token[:12] + "…" + t.token[-4:])
                        if t.token and len(t.token) > 20
                        else "(short)"
                    ),
                    is_active=bool(t.is_active),
                    validation_status=t.validation_status or "unknown",
                    last_validated=(t.last_validated.isoformat() if t.last_validated else None),
                    error_message=t.error_message,
                    permissions=(t.permissions if isinstance(t.permissions, list) else None),
                )
            )
        else:
            rows.append(
                CommunityTokenRow(
                    region_id=r.id,
                    region_name=r.name,
                    region_code=r.code,
                    vk_group_id=r.vk_group_id,
                    community_id=cid,
                )
            )
    return rows


@router.put("/communities/{community_id}", response_model=CommunityTokenRow)
async def upsert_community_token(
    community_id: int,
    request: CommunityTokenUpsert,
    db: AsyncSession = Depends(get_db_session),
):
    """Создать или обновить community-токен для конкретного сообщества."""
    from database.models import Region

    cid = abs(int(community_id))

    region_q = await db.execute(
        select(Region).where((Region.vk_group_id == cid) | (Region.vk_group_id == -cid))
    )
    region = region_q.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail=f"No region with vk_group_id={community_id}")

    existing_q = await db.execute(select(VKToken).where(VKToken.community_id == cid))
    token = existing_q.scalar_one_or_none()

    if token is None:
        token = VKToken(
            name=f"COMM_{cid}",
            token=request.token.strip(),
            community_id=cid,
            is_active=True,
            validation_status="unknown",
        )
        db.add(token)
    else:
        token.token = request.token.strip()
        token.is_active = True

    if request.validate_token:
        v = await validate_community_token(token.token, cid)
        token.validation_status = "valid" if v["is_valid"] else "invalid"
        token.error_message = v.get("error_message")
        token.user_info = v.get("user_info")
        token.permissions = v.get("permissions")
        token.last_validated = datetime.now()

    await db.commit()
    await db.refresh(token)

    return CommunityTokenRow(
        region_id=region.id,
        region_name=region.name,
        region_code=region.code,
        vk_group_id=region.vk_group_id,
        community_id=cid,
        token_id=token.id,
        token_name=token.name,
        token_masked=(
            (token.token[:12] + "…" + token.token[-4:])
            if token.token and len(token.token) > 20
            else "(short)"
        ),
        is_active=bool(token.is_active),
        validation_status=token.validation_status or "unknown",
        last_validated=(token.last_validated.isoformat() if token.last_validated else None),
        error_message=token.error_message,
        permissions=token.permissions if isinstance(token.permissions, list) else None,
    )


@router.post("/communities/{community_id}/validate", response_model=TokenValidationResponse)
async def validate_community_token_endpoint(
    community_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    cid = abs(int(community_id))
    q = await db.execute(select(VKToken).where(VKToken.community_id == cid))
    token = q.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail=f"No community token for {community_id}")

    v = await validate_community_token(token.token, cid)
    token.validation_status = "valid" if v["is_valid"] else "invalid"
    token.error_message = v.get("error_message")
    token.user_info = v.get("user_info")
    token.permissions = v.get("permissions")
    token.last_validated = datetime.now()
    await db.commit()

    return TokenValidationResponse(
        name=token.name,
        is_valid=v["is_valid"],
        validation_status=token.validation_status,
        error_message=v.get("error_message"),
        user_info=v.get("user_info"),
        permissions=v.get("permissions"),
    )


@router.delete("/communities/{community_id}")
async def delete_community_token(
    community_id: int,
    db: AsyncSession = Depends(get_db_session),
):
    cid = abs(int(community_id))
    q = await db.execute(select(VKToken).where(VKToken.community_id == cid))
    token = q.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail=f"No community token for {community_id}")
    await db.execute(delete(VKToken).where(VKToken.id == token.id))
    await db.commit()
    return {"success": True, "community_id": cid}


# ---------------------------------------------------------------------------
# TokenPolicy: ручной disable / enable (миграция 014, 2026-05-27).
# Регистрируется ДО `/{token_name}` — иначе FastAPI поймает path как имя токена.
# ---------------------------------------------------------------------------


@router.post("/{token_name}/disable", response_model=TokenResponse)
async def disable_token(
    token_name: str,
    payload: TokenDisableRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Отключить токен на ``hours`` часов (manual disable).

    Записывает ``disabled_until = now() + hours`` в ``vk_tokens``. Применяется
    TokenPolicy: токен сразу выпадает из ``pick(...)`` для всех операций.

    Если запись в БД ещё нет (а имя есть в env) — создаётся новая. Возвращает
    обновлённую запись.
    """
    from modules.vk_token_router import TokenPolicy

    upper = token_name.upper()
    policy = TokenPolicy(db)
    ok = await policy.disable(upper, hours=payload.hours, reason=payload.reason)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Token {upper} not found in env or DB")
    result = await db.execute(
        select(VKToken).where(VKToken.name == upper, VKToken.community_id.is_(None))
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=500, detail="disable succeeded but row not found")
    return TokenResponse(**token.to_dict())


@router.post("/{token_name}/enable", response_model=TokenResponse)
async def enable_token(
    token_name: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Снять ручной/авто-disable: ``disabled_until=NULL`` + сброс ``consecutive_errors``.

    404 если записи в БД нет (значит токен и так не был disabled — нечего
    «включать»).
    """
    from modules.vk_token_router import TokenPolicy

    upper = token_name.upper()
    policy = TokenPolicy(db)
    ok = await policy.enable(upper)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Token {upper} not in DB")
    result = await db.execute(
        select(VKToken).where(VKToken.name == upper, VKToken.community_id.is_(None))
    )
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=500, detail="enable succeeded but row not found")
    return TokenResponse(**token.to_dict())


class TokenPublishRoleRequest(BaseModel):
    """POST /api/tokens/{name}/publish-role body."""

    enabled: bool = Field(
        ...,
        description="True — разрешить публиковать (role='publish'); False — снять (NULL).",
    )


@router.post("/{token_name}/publish-role", response_model=TokenResponse)
async def set_token_publish_role(
    token_name: str,
    payload: TokenPublishRoleRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """UI-override: пометить user-токен как разрешённый для публикаций.

    ``role='publish'`` добавляет токен к ``VK_PUBLISH_TOKEN_NAMES`` **аддитивно**
    (``TokenPolicy`` объединяет env-whitelist с БД-ролью) — без правки env и
    рестарта. Снятие (``enabled=False``) пишет ``role=NULL``. Лёгкий эндпоинт:
    токен НЕ перевалидируется. Community-токены публикуют в свою группу
    независимо от роли → для них 400.
    """
    upper = token_name.upper()
    result = await db.execute(select(VKToken).where(VKToken.name == upper))
    token = result.scalar_one_or_none()
    if not token:
        raise HTTPException(status_code=404, detail=f"Token {upper} not found")
    if token.community_id is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "Роль публикации применима только к user-токенам "
                "(community-токен публикует в свою группу)."
            ),
        )
    token.role = "publish" if payload.enabled else None
    await db.commit()
    await db.refresh(token)
    logger.info("Token %s publish-role set to %r", upper, token.role)
    return TokenResponse(**token.to_dict())


@router.get("/{token_name}", response_model=TokenResponse)
async def get_token(token_name: str, db: AsyncSession = Depends(get_db_session)):
    """Получить конкретный токен"""
    try:
        result = await db.execute(select(VKToken).where(VKToken.name == token_name.upper()))
        token = result.scalar_one_or_none()

        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")

        return TokenResponse(**token.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting token {token_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{token_name}", response_model=TokenResponse)
async def update_token(
    token_name: str,
    request: TokenUpdateRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """Обновить токен"""
    try:
        # Найти токен
        result = await db.execute(select(VKToken).where(VKToken.name == token_name.upper()))
        token = result.scalar_one_or_none()

        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")

        # Обновить флаг активности (если передан)
        if request.is_active is not None:
            token.is_active = request.is_active

        # Обновить токен (если передан)
        if request.token is not None and request.token.strip() != "":
            token.token = request.token.strip()
            token.validation_status = "unknown"
            token.error_message = None

        # Валидировать токен если требуется
        if request.validate_token:
            if token.community_id:
                validation_result = await validate_community_token(token.token, token.community_id)
            else:
                validation_result = await validate_single_token(token.token)
            token.validation_status = "valid" if validation_result["is_valid"] else "invalid"
            token.error_message = validation_result.get("error_message")
            token.user_info = validation_result.get("user_info")
            token.permissions = validation_result.get("permissions")
            token.last_validated = datetime.now()

        await db.commit()
        await db.refresh(token)

        logger.info(f"Token {token_name} updated successfully")
        return TokenResponse(**token.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating token {token_name}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add", response_model=TokenResponse)
async def add_token(request: TokenCreateRequest, db: AsyncSession = Depends(get_db_session)):
    """Создать новый токен"""
    try:
        token_name = request.name.strip().upper()
        if not token_name:
            raise HTTPException(status_code=400, detail="Token name is required")

        # Check duplicate
        existing = await db.execute(select(VKToken).where(VKToken.name == token_name))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=409, detail=f"Token {token_name} already exists")

        vk_token = VKToken(
            name=token_name,
            token=request.token.strip(),
            community_id=abs(request.community_id) if request.community_id else None,
            is_active=True,
            validation_status="unknown",
            error_message=None,
            permissions=None,
            user_info=None,
        )

        if request.validate_token:
            if vk_token.community_id:
                validation_result = await validate_community_token(
                    vk_token.token, vk_token.community_id
                )
            else:
                validation_result = await validate_single_token(vk_token.token)
            vk_token.validation_status = "valid" if validation_result["is_valid"] else "invalid"
            vk_token.error_message = validation_result.get("error_message")
            vk_token.user_info = validation_result.get("user_info")
            vk_token.permissions = validation_result.get("permissions")
            vk_token.last_validated = datetime.now()

        db.add(vk_token)
        await db.commit()
        await db.refresh(vk_token)
        return TokenResponse(**vk_token.to_dict())

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error adding token: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/{token_name}")
async def delete_token(token_name: str, db: AsyncSession = Depends(get_db_session)):
    """Удалить токен"""
    try:
        token_name = token_name.strip().upper()
        result = await db.execute(select(VKToken).where(VKToken.name == token_name))
        token = result.scalar_one_or_none()
        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")

        await db.execute(delete(VKToken).where(VKToken.name == token_name))
        await db.commit()
        return {"success": True, "name": token_name}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting token {token_name}: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{token_name}/validate", response_model=TokenValidationResponse)
async def validate_token(token_name: str, db: AsyncSession = Depends(get_db_session)):
    """Валидировать токен"""
    try:
        # Найти токен
        result = await db.execute(select(VKToken).where(VKToken.name == token_name.upper()))
        token = result.scalar_one_or_none()

        if not token:
            raise HTTPException(status_code=404, detail=f"Token {token_name} not found")

        if not token.token:
            return TokenValidationResponse(
                name=token.name,
                is_valid=False,
                validation_status="invalid",
                error_message="Token is empty",
                user_info=None,
                permissions=None,
            )

        # Валидировать токен (community-токены — отдельная ветка, у них нет users.get)
        if token.community_id:
            validation_result = await validate_community_token(token.token, token.community_id)
        else:
            validation_result = await validate_single_token(token.token)

        # Обновить статус в БД
        token.validation_status = "valid" if validation_result["is_valid"] else "invalid"
        token.error_message = validation_result.get("error_message")
        token.user_info = validation_result.get("user_info")
        token.permissions = validation_result.get("permissions")
        token.last_validated = datetime.now()

        await db.commit()

        return TokenValidationResponse(
            name=token.name,
            is_valid=validation_result["is_valid"],
            validation_status=token.validation_status,
            error_message=validation_result.get("error_message"),
            user_info=validation_result.get("user_info"),
            permissions=validation_result.get("permissions"),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error validating token {token_name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/validate-all", response_model=List[TokenValidationResponse])
async def validate_all_tokens(db: AsyncSession = Depends(get_db_session)):
    """Валидировать все токены"""
    try:
        result = await db.execute(select(VKToken))
        tokens = result.scalars().all()

        validation_results = []

        for token in tokens:
            if not token.token:
                validation_results.append(
                    TokenValidationResponse(
                        name=token.name,
                        is_valid=False,
                        validation_status="invalid",
                        error_message="Token is empty",
                        user_info=None,
                        permissions=None,
                    )
                )
                continue

            # Валидировать токен (community-токены — отдельная ветка)
            if token.community_id:
                validation_result = await validate_community_token(token.token, token.community_id)
            else:
                validation_result = await validate_single_token(token.token)

            # Обновить статус в БД
            token.validation_status = "valid" if validation_result["is_valid"] else "invalid"
            token.error_message = validation_result.get("error_message")
            token.user_info = validation_result.get("user_info")
            token.permissions = validation_result.get("permissions")
            token.last_validated = datetime.now()

            validation_results.append(
                TokenValidationResponse(
                    name=token.name,
                    is_valid=validation_result["is_valid"],
                    validation_status=token.validation_status,
                    error_message=validation_result.get("error_message"),
                    user_info=validation_result.get("user_info"),
                    permissions=validation_result.get("permissions"),
                )
            )

        await db.commit()

        logger.info(f"Validated {len(tokens)} tokens")
        return validation_results

    except Exception as e:
        logger.error(f"Error validating all tokens: {e}")
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


async def validate_community_token(token: str, community_id: int) -> Dict[str, Any]:
    """Валидировать community access token конкретного сообщества.

    Community-токены не имеют `users.get` — они привязаны к группе. Признак
    валидности: `messages.getConversations` (без group_id) проходит без [15]
    Access denied. Для info берём `groups.getById` под этим же токеном.
    """
    import vk_api

    try:
        session = vk_api.VkApi(token=token)
        api = session.get_api()

        # 1) Проверим что токен реально принадлежит этому сообществу (groups.getById
        # доступен community-токену про его собственную группу).
        group_info = None
        try:
            r = api.groups.getById(group_id=abs(int(community_id)))
            if isinstance(r, list) and r:
                group_info = r[0]
            elif isinstance(r, dict) and r.get("groups"):
                group_info = r["groups"][0]
        except vk_api.exceptions.ApiError as e:
            return {
                "is_valid": False,
                "error_message": f"groups.getById failed: {e}",
                "user_info": None,
                "permissions": None,
            }

        # 2) Проверим scope `messages` — главная цель этого токена.
        permissions: List[str] = []
        try:
            api.messages.getConversations(count=1)
            permissions.append("messages.read")
        except vk_api.exceptions.ApiError as e:
            return {
                "is_valid": False,
                "error_message": f"messages.getConversations failed: {e}",
                "user_info": group_info,
                "permissions": [],
            }

        # 3) Дополнительно проверим wall.get (часто доступен community-токену) —
        # это не критично для валидности, но полезно для UI.
        try:
            api.wall.get(owner_id=-abs(int(community_id)), count=1)
            permissions.append("wall.read")
        except vk_api.exceptions.ApiError:
            pass

        return {
            "is_valid": True,
            "error_message": None,
            "user_info": group_info,
            "permissions": permissions,
        }
    except Exception as e:
        return {
            "is_valid": False,
            "error_message": str(e),
            "user_info": None,
            "permissions": None,
        }


async def validate_single_token(token: str) -> Dict[str, Any]:
    """Валидировать один токен"""
    try:
        vk_client = VKClient(token)
        user_info = await vk_client.get_user_info()

        if not user_info:
            return {
                "is_valid": False,
                "error_message": "Failed to get user info",
                "user_info": None,
                "permissions": None,
            }

        # Получить права доступа.
        # VKClient.get_posts/get_groups/get_messages глотают ApiError и возвращают None
        # при отказе VK — поэтому try/except не сработает, нужно проверять возвращаемое
        # значение на не-None, чтобы не репортить «есть permission», когда VK его отозвал.
        permissions = []
        if await vk_client.get_posts(owner_id=-1, count=1) is not None:
            permissions.append("wall.read")
        if await vk_client.get_groups(count=1) is not None:
            permissions.append("groups.read")
        if await vk_client.get_messages(count=1) is not None:
            permissions.append("messages.read")

        return {
            "is_valid": True,
            "error_message": None,
            "user_info": user_info,
            "permissions": permissions,
        }

    except Exception as e:
        return {
            "is_valid": False,
            "error_message": str(e),
            "user_info": None,
            "permissions": None,
        }
