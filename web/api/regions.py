"""
Regions API endpoints
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, validator
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import Community, CommunityCandidate, Post, Region
from modules.digest_template import (
    STANDARD_TOPICS,
    compute_effective_digest_settings,
    topic_to_default_hashtag,
)
from modules.geo.geocoder import geocode, haversine_km
from utils.cache import cache, invalidate_cache

router = APIRouter()


REGION_KINDS = ("raion", "oblast", "strana")


def _parse_neighbor_tokens(raw: str | None) -> List[str]:
    """Разбить CSV-строку соседей в список непустых токенов (запятая или ;)."""
    if not raw:
        return []
    return [t.strip() for t in raw.replace(";", ",").split(",") if t.strip()]


def _region_label_variants(name: str | None, center: str | None) -> set[str]:
    """Варианты человекочитаемых меток региона для матчинга соседей по имени.

    Исторические ``Region.neighbors`` забиты **голыми названиями городов**
    («лебяжье», «советск», «пижанка»), тогда как ``Region.name`` хранится в
    формате «ЛЕБЯЖЬЕ - ИНФО» / «Тужа, Кировская область». Без нормализации
    суффиксов прямой матч имени проваливался и реальные соседи терялись.

    Возвращает набор lowercase-вариантов:

    * полное имя;
    * имя без хвоста после первой запятой («Тужа, Кировская область» → «тужа»);
    * имя без суффикса «- ИНФО» при любом виде тире (``-`` / ``–`` / ``—``);
    * ``center_city`` (как есть и без гео-хвоста после запятой).
    """
    variants: set[str] = set()
    for label in (name, center):
        if not label:
            continue
        base = str(label).strip()
        if not base:
            continue
        variants.add(base.lower())
        head = base.split(",", 1)[0].strip()  # отрезаем «, Кировская область»
        if head:
            variants.add(head.lower())
        for dash in ("-", "–", "—"):
            marker = f"{dash} ИНФО"
            idx = head.upper().rfind(marker.upper())
            if idx != -1:
                trimmed = head[:idx].strip()
                if trimmed:
                    variants.add(trimmed.lower())
    return variants


async def _normalize_neighbor_codes(db: AsyncSession, raw: str | None, self_code: str) -> List[str]:
    """Привести список соседей к валидным кодам регионов.

    Движок соседского обмена (``modules.cascaded_digest.run_neighbor_digest``)
    матчит соседей по ``Region.code.in_(codes)`` — поэтому в ``Region.neighbors``
    должны лежать именно **коды** регионов (латиница), а не русские названия.
    Исторически часть данных была забита названиями («кукмор», «балтаси»,
    «лебяжье»), из-за чего обмен молча не находил соседей. Эта функция:

    * принимает токен как код региона (case-insensitive) **или**
      как ``name`` / ``center_city`` (в т.ч. «ЛЕБЯЖЬЕ - ИНФО» → «лебяжье»
      через :func:`_region_label_variants`) и резолвит в код;
    * отбрасывает неизвестные токены и сам регион (само-сосед запрещён);
    * возвращает отсортированный уникальный список кодов.
    """
    tokens = _parse_neighbor_tokens(raw)
    if not tokens:
        return []

    result = await db.execute(select(Region.code, Region.name, Region.center_city))
    rows = result.all()
    by_code = {r[0].lower(): r[0] for r in rows}
    by_name: Dict[str, str] = {}
    for code, name, center in rows:
        for variant in _region_label_variants(name, center):
            by_name.setdefault(variant, code)

    resolved: List[str] = []
    seen: set[str] = set()
    self_lower = (self_code or "").lower()
    for tok in tokens:
        low = tok.lower()
        code = by_code.get(low) or by_name.get(low)
        if not code:
            continue
        if code.lower() == self_lower:
            continue
        if code not in seen:
            seen.add(code)
            resolved.append(code)
    return sorted(resolved)


async def _sync_bidirectional_neighbors(
    db: AsyncSession, self_code: str, old_csv: str | None, new_codes: List[str]
) -> None:
    """Сделать связь соседей обоюдной (двунаправленной).

    Если у региона A в соседях появился B — у B тоже должен появиться A; если
    A убрал B из соседей — A исчезает из соседей B. Меняем только затронутые
    регионы (added ∪ removed), не трогая остальных. Без ``commit`` — вызывающий
    делает его в общей транзакции вместе с записью самого региона.
    """
    old_set = {c.lower() for c in _parse_neighbor_tokens(old_csv)}
    new_set = {c.lower() for c in new_codes}
    added = [c for c in new_codes if c.lower() not in old_set]
    removed = [c for c in _parse_neighbor_tokens(old_csv) if c.lower() not in new_set]

    affected = {c for c in added} | {c for c in removed}
    if not affected:
        return

    result = await db.execute(select(Region).where(Region.code.in_(list(affected))))
    regions = {r.code: r for r in result.scalars().all()}

    added_lower = {c.lower() for c in added}
    for code, region in regions.items():
        cur = _parse_neighbor_tokens(region.neighbors)
        cur_lower = {c.lower() for c in cur}
        if code.lower() in added_lower:
            if self_code.lower() not in cur_lower:
                cur.append(self_code)
        else:  # removed
            cur = [c for c in cur if c.lower() != self_code.lower()]
        region.neighbors = ",".join(sorted(set(cur))) or None


def _geocodable_label(name: str | None, center_city: str | None = None) -> str | None:
    """Имя для геокодинга центра региона.

    Предпочитаем ``center_city`` (без гео-хвоста после запятой); при пустом —
    голову ``name`` без суффикса «- ИНФО» (та же чистка, что в
    :func:`_region_label_variants`, но возвращаем единственную метку с
    сохранением регистра — для геокода и отображения). ``«МАЛМЫЖ - ИНФО»`` →
    ``«МАЛМЫЖ»``, ``«Тужа, Кировская область»`` → ``«Тужа»``.
    """
    center = (center_city or "").split(",", 1)[0].strip()
    if center:
        return center
    base = (name or "").strip()
    if not base:
        return None
    head = base.split(",", 1)[0].strip()  # отрезаем «, Кировская область»
    for dash in ("-", "–", "—"):
        idx = head.upper().rfind(f"{dash} ИНФО")
        if idx != -1:
            head = head[:idx].strip()
            break
    return head or None


async def _region_geo_hint(db: AsyncSession, region: Region) -> Optional[str]:
    """Имя родительской области как гео-подсказка для дизамбигуации омонимов.

    «Советск»/«Лебяжье» есть в нескольких регионах РФ — без области Nominatim
    промахивается. Берём ``_geocodable_label`` родителя (``«Кировская область»``).
    """
    if not region.parent_region_id:
        return None
    result = await db.execute(
        select(Region.name, Region.center_city).where(Region.id == region.parent_region_id)
    )
    row = result.first()
    if not row:
        return None
    return _geocodable_label(row[0], row[1])


async def _ensure_region_coords(
    db: AsyncSession, region: Region, *, force: bool = False
) -> Optional[Dict[str, Any]]:
    """Вернуть закэшированные координаты центра региона или геокодировать их.

    Кэш — ``region.config['geo'] = {lat, lon, label, source, geocoded_at}``.
    При ``force`` геокодируем заново. Возвращает dict с ``lat``/``lon`` либо
    ``None`` (пустой лейбл / геокод не удался). При успешном геокоде коммитит
    (паттерн записи JSON — переприсваивание ``region.config``).
    """
    cfg: Dict[str, Any] = region.config if isinstance(region.config, dict) else {}
    cached = cfg.get("geo")
    if not force and isinstance(cached, dict) and "lat" in cached and "lon" in cached:
        return cached

    label = _geocodable_label(region.name, region.center_city)
    if not label:
        return None
    hint = await _region_geo_hint(db, region)
    coords = await geocode(label, region_hint=hint)
    if not coords:
        return None

    geo = {
        "lat": coords[0],
        "lon": coords[1],
        "label": label,
        "source": "nominatim",
        "geocoded_at": datetime.utcnow().isoformat(),
    }
    cfg = dict(cfg)
    cfg["geo"] = geo
    region.config = cfg
    region.updated_at = datetime.utcnow()
    await db.commit()
    return geo


def _to_negative_owner_id(v: Optional[int]) -> Optional[int]:
    """Нормализует ``vk_group_id`` к VK owner-форме (отрицательный id группы).

    Инвариант колонки ``regions.vk_group_id`` — отрицательное число (как
    ``-168170001``): VK wall.post/wall.repost требуют отрицательный ``owner_id``
    для групп. Модератор в /regions легко вводит «голый» положительный id —
    так в БД попал ``tuzha=239050321`` (единственный положительный из 17).
    Рантайм-публикация это переживает (весь publish-путь делает ``-abs``), но
    положительный id нарушает инвариант и сбивает прямые сравнения по id без
    ``abs``. Нормализуем на входе, чтобы он не повторился. ``None`` (поле не
    задано в update) пропускаем без изменений.
    """
    if v is None:
        return v
    return -abs(int(v))


class RegionCreate(BaseModel):
    """Region create model"""

    code: str = Field(
        ...,
        min_length=2,
        max_length=50,
        description="Уникальный код региона (например, 'mi', 'nolinsk')",
    )
    name: str = Field(..., min_length=2, max_length=200, description="Название региона")
    vk_group_id: Optional[int] = Field(
        None, description="ID главной VK группы (отрицательное число)"
    )
    telegram_channel: Optional[str] = Field(
        None, max_length=100, description="Telegram канал (например, @malmig_info)"
    )
    neighbors: Optional[str] = Field(
        None, max_length=500, description="Соседние регионы через запятую"
    )
    local_hashtags: Optional[str] = Field(None, description="Локальные хештеги")
    is_active: bool = Field(True, description="Активен ли регион")
    # Geo (модуль авто-регистрации, миграция 011). До PR #31 эти поля были
    # в `Region`-модели, но не в `RegionCreate` — wizard их терял, и discovery
    # потом падал с "center_city is empty".
    vk_city_id: Optional[int] = Field(None, description="VK API city_id для гео-поиска")
    center_city: Optional[str] = Field(
        None, max_length=200, description="Имя центра района: 'Малмыж'"
    )
    # Иерархия (миграция 015) — тип региона и родитель.
    kind: str = Field(
        "raion",
        description="Тип региона: raion | oblast | strana",
        pattern="^(raion|oblast|strana)$",
    )
    parent_region_id: Optional[int] = Field(
        None,
        description=(
            "ID родителя в иерархии: raion → oblast.id, oblast → strana.id, " "strana → null"
        ),
    )

    _normalize_vk_group_id = validator("vk_group_id", allow_reuse=True)(_to_negative_owner_id)


class RegionUpdate(BaseModel):
    """Region update model"""

    name: Optional[str] = Field(None, min_length=2, max_length=200)
    vk_group_id: Optional[int] = None
    telegram_channel: Optional[str] = Field(None, max_length=100)
    neighbors: Optional[str] = Field(None, max_length=500)
    local_hashtags: Optional[str] = None
    is_active: Optional[bool] = None
    kind: Optional[str] = Field(None, pattern="^(raion|oblast|strana)$")
    parent_region_id: Optional[int] = None

    _normalize_vk_group_id = validator("vk_group_id", allow_reuse=True)(_to_negative_owner_id)


class RegionResponse(BaseModel):
    """Region response model"""

    id: int
    code: str
    name: str
    vk_group_id: int | None
    telegram_channel: str | None
    neighbors: str | None
    is_active: bool
    created_at: str
    communities_count: int = 0
    posts_count: int = 0
    # Discovery (миграция 013) — для UI на /regions.
    # ``last_discovery_at`` — ISO-timestamp последнего запуска или None.
    # ``pending_candidates_count`` — сколько кандидатов в статусе ``pending``
    # ждут проверки модератором (показываем бейдж «🔔 N» на карточке).
    # ``has_discovery_config`` — заполнен ли config.localities (без него
    # discovery невозможен — кнопка «Найти новые» должна быть disabled).
    last_discovery_at: str | None = None
    pending_candidates_count: int = 0
    has_discovery_config: bool = False
    # Иерархия (миграция 015) — тип и родитель.
    kind: str = "raion"
    parent_region_id: int | None = None

    class Config:
        from_attributes = True


class DigestTemplateSettingsModel(BaseModel):
    title: str = Field(..., description="Заголовок дайджеста")
    footer: str = Field("", description="Подвал дайджеста")
    include_source_links: bool = Field(
        True, description="Показывать кликабельный источник под новостью"
    )
    include_topic_hashtag: bool = Field(True, description="Добавлять хештег темы в конце")
    include_region_hashtags: bool = Field(
        False, description="Добавлять локальные хештеги региона в конце"
    )
    topic_hashtag_override: str = Field(
        "", description="Переопределение хештега темы (если пусто — берём дефолт)"
    )


class DigestTemplatePayload(BaseModel):
    defaults: Optional[DigestTemplateSettingsModel] = None
    by_topic: Optional[Dict[str, DigestTemplateSettingsModel]] = None


class DigestTemplateResponse(BaseModel):
    region_code: str
    region_name: str
    topics: List[str]
    # Raw override stored in Region.config.digest_template (may be empty)
    raw_override: Dict[str, Any]
    # Effective merged settings per topic
    effective_by_topic: Dict[str, DigestTemplateSettingsModel]
    # Effective defaults (after applying region defaults override)
    effective_defaults: DigestTemplateSettingsModel


@router.get("/{region_code}/digest-template", response_model=DigestTemplateResponse)
async def get_region_digest_template(
    region_code: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get digest template configuration for region, merged with defaults.
    """
    result = await db.execute(select(Region).where(Region.code == region_code))
    region = result.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    topics = STANDARD_TOPICS

    # Compute effective defaults by merging base + region defaults (no topic override)
    base_settings, raw = compute_effective_digest_settings(region, topic="")
    effective_defaults = DigestTemplateSettingsModel(
        title=base_settings.title,
        footer=base_settings.footer,
        include_source_links=base_settings.include_source_links,
        include_topic_hashtag=base_settings.include_topic_hashtag,
        include_region_hashtags=base_settings.include_region_hashtags,
        topic_hashtag_override=base_settings.topic_hashtag_override,
    )

    effective_by_topic: Dict[str, DigestTemplateSettingsModel] = {}
    for t in topics:
        s, _ = compute_effective_digest_settings(region, topic=t)
        # Provide a sensible default hashtag if none configured
        hashtag_override = s.topic_hashtag_override or topic_to_default_hashtag(t)
        effective_by_topic[t] = DigestTemplateSettingsModel(
            title=s.title,
            footer=s.footer,
            include_source_links=s.include_source_links,
            include_topic_hashtag=s.include_topic_hashtag,
            include_region_hashtags=s.include_region_hashtags,
            topic_hashtag_override=hashtag_override,
        )

    return DigestTemplateResponse(
        region_code=region.code,
        region_name=region.name,
        topics=topics,
        raw_override=raw or {},
        effective_by_topic=effective_by_topic,
        effective_defaults=effective_defaults,
    )


@router.put("/{region_code}/digest-template", response_model=DigestTemplateResponse)
async def put_region_digest_template(
    region_code: str,
    payload: DigestTemplatePayload,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Save digest template overrides into Region.config.digest_template.
    """
    result = await db.execute(select(Region).where(Region.code == region_code))
    region = result.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    cfg: Dict[str, Any] = region.config if isinstance(region.config, dict) else {}

    new_dt: Dict[str, Any] = {"defaults": {}, "by_topic": {}}
    if payload.defaults is not None:
        new_dt["defaults"] = payload.defaults.model_dump()
    if payload.by_topic is not None:
        new_dt["by_topic"] = {k: v.model_dump() for k, v in payload.by_topic.items()}

    # If nothing is set, remove override entirely
    if not new_dt["defaults"] and not new_dt["by_topic"]:
        cfg.pop("digest_template", None)
    else:
        cfg["digest_template"] = new_dt

    region.config = cfg
    region.updated_at = datetime.utcnow()
    await db.commit()

    await invalidate_cache("regions:*")

    # Return fresh merged view
    return await get_region_digest_template(region_code=region_code, db=db)


@router.post("/{region_code}/digest-template/reset", response_model=DigestTemplateResponse)
async def reset_region_digest_template(
    region_code: str,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Reset digest template override for whole region to defaults.
    """
    result = await db.execute(select(Region).where(Region.code == region_code))
    region = result.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    cfg: Dict[str, Any] = region.config if isinstance(region.config, dict) else {}
    cfg.pop("digest_template", None)
    region.config = cfg
    region.updated_at = datetime.utcnow()
    await db.commit()

    await invalidate_cache("regions:*")

    return await get_region_digest_template(region_code=region_code, db=db)


class ResetDigestTemplateTopicRequest(BaseModel):
    topic: str = Field(..., description="Тема для сброса (например, 'Культура')")


@router.post("/{region_code}/digest-template/reset-topic", response_model=DigestTemplateResponse)
async def reset_region_digest_template_topic(
    region_code: str,
    request: ResetDigestTemplateTopicRequest,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Reset digest template override for a single topic (removes by_topic[topic]).
    """
    result = await db.execute(select(Region).where(Region.code == region_code))
    region = result.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    cfg: Dict[str, Any] = region.config if isinstance(region.config, dict) else {}
    dt: Dict[str, Any] = (
        cfg.get("digest_template") if isinstance(cfg.get("digest_template"), dict) else {}
    )
    by_topic: Dict[str, Any] = dt.get("by_topic") if isinstance(dt.get("by_topic"), dict) else {}

    by_topic.pop(request.topic, None)
    dt["by_topic"] = by_topic

    # If nothing left, remove whole digest_template
    defaults = dt.get("defaults") if isinstance(dt.get("defaults"), dict) else {}
    if not defaults and not by_topic:
        cfg.pop("digest_template", None)
    else:
        cfg["digest_template"] = dt

    region.config = cfg
    region.updated_at = datetime.utcnow()
    await db.commit()

    await invalidate_cache("regions:*")

    return await get_region_digest_template(region_code=region_code, db=db)


# ──────────────────────────────────────────────────────────────────────────
# Diagnostics — «прогон пайплайна без публикации» (dry_run).
# Парсит/фильтрует/собирает дайджест региона+темы, НИЧЕГО не публикуя и не
# записывая в БД (см. parse_and_publish_theme(dry_run=True)). Длинная операция
# (реальный VK-парсинг), поэтому ставится в Celery и опрашивается по task_id —
# как discovery (/api/discovery/trigger-async + /task/{id}).
# ──────────────────────────────────────────────────────────────────────────


@router.post("/diagnostics/task/{task_id}/status", include_in_schema=False)
@router.get("/diagnostics/task/{task_id}/status")
async def get_diagnostics_task_status(task_id: str):
    """Статус diagnostics-задачи (polling из UI). См. discovery /task/{id}."""
    try:
        from celery.result import AsyncResult

        from tasks.celery_app import app as celery_app
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"Celery не доступен: {e}")

    ar = AsyncResult(task_id, app=celery_app)
    state = ar.state
    ready = ar.ready()
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "state": state,
        "ready": ready,
        "result": None,
        "error": None,
    }
    if state == "SUCCESS":
        try:
            payload["result"] = ar.result
        except Exception as e:  # pragma: no cover
            payload["error"] = f"не удалось получить result: {e}"
    elif state == "FAILURE":
        try:
            payload["error"] = str(ar.result)
        except Exception:  # pragma: no cover
            payload["error"] = "задача завершилась с ошибкой"
    return payload


@router.post("/{region_code}/diagnostics")
async def run_region_diagnostics(
    region_code: str,
    theme: str = "novost",
    db: AsyncSession = Depends(get_db_session),
):
    """Поставить dry-run прогон пайплайна региона+темы (без публикации).

    Возвращает ``{task_id, state}``; UI опрашивает
    ``/diagnostics/task/{task_id}/status`` до ``ready``. Результат содержит
    ``would_publish`` (что попало бы в дайджест) + статистику фильтрации.
    """
    # Проверяем, что регион существует — ранний 404 вместо «висящей» задачи.
    exists = await db.execute(select(Region.id).where(Region.code == region_code))
    if exists.first() is None:
        raise HTTPException(status_code=404, detail=f"Region '{region_code}' not found")

    try:
        from tasks.celery_app import app as celery_app
    except ImportError as e:  # pragma: no cover
        raise HTTPException(status_code=503, detail=f"Celery не доступен: {e}")

    try:
        task = celery_app.send_task(
            "tasks.parsing_scheduler_tasks.parse_and_publish_theme",
            kwargs={"region_code": region_code, "theme": theme, "dry_run": True},
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Не удалось поставить задачу: {e}")

    return {"task_id": task.id, "state": task.state, "region_code": region_code, "theme": theme}


@router.get("/", response_model=List[RegionResponse])
@cache(ttl=600, key_prefix="regions")  # Cache for 10 minutes
async def get_regions(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db_session)):
    """Get all regions with optimized counts"""
    # Get all regions
    result = await db.execute(select(Region).offset(skip).limit(limit))
    regions = result.scalars().all()

    # Get counts in bulk (ONE query for all communities, ONE query for all posts)
    region_ids = [r.id for r in regions]

    # Count communities per region
    comm_counts_result = await db.execute(
        select(Community.region_id, func.count(Community.id))
        .where(Community.region_id.in_(region_ids))
        .group_by(Community.region_id)
    )
    comm_counts = {row[0]: row[1] for row in comm_counts_result.all()}

    # Count posts per region
    posts_counts_result = await db.execute(
        select(Post.region_id, func.count(Post.id))
        .where(Post.region_id.in_(region_ids))
        .group_by(Post.region_id)
    )
    posts_counts = {row[0]: row[1] for row in posts_counts_result.all()}

    # Pending candidates per region — для бейджа «🔔 N кандидатов на проверку».
    pending_counts_result = await db.execute(
        select(CommunityCandidate.region_id, func.count(CommunityCandidate.id))
        .where(
            CommunityCandidate.region_id.in_(region_ids),
            CommunityCandidate.status == "pending",
        )
        .group_by(CommunityCandidate.region_id)
    )
    pending_counts = {row[0]: row[1] for row in pending_counts_result.all()}

    # Build response
    regions_with_counts = []
    for region in regions:
        cfg = region.config if isinstance(region.config, dict) else {}
        loc_list = cfg.get("localities") or []
        region_dict = {
            "id": region.id,
            "code": region.code,
            "name": region.name,
            "vk_group_id": region.vk_group_id,
            "telegram_channel": region.telegram_channel,
            "neighbors": region.neighbors,
            "is_active": region.is_active,
            "created_at": (
                region.created_at.isoformat()
                if hasattr(region, "created_at") and region.created_at
                else ""
            ),
            "communities_count": comm_counts.get(region.id, 0),
            "posts_count": posts_counts.get(region.id, 0),
            "last_discovery_at": (
                region.last_discovery_at.isoformat() if region.last_discovery_at else None
            ),
            "pending_candidates_count": pending_counts.get(region.id, 0),
            "has_discovery_config": bool(loc_list and region.center_city),
            "kind": getattr(region, "kind", None) or "raion",
            "parent_region_id": getattr(region, "parent_region_id", None),
        }
        regions_with_counts.append(region_dict)

    return regions_with_counts


class NeighborSuggestion(BaseModel):
    """Один кандидат-сосед с расстоянием до центра цели."""

    code: str
    name: str
    distance_km: float
    within_threshold: bool


class SuggestNeighborsResponse(BaseModel):
    """Ответ ``GET /suggest-neighbors``: цель + ранжированные кандидаты."""

    target: Dict[str, Any]
    suggestions: List[NeighborSuggestion]
    not_geocoded: List[str]


# ВАЖНО: объявлено ДО ``@router.get("/{region_code}")`` — иначе FastAPI примет
# «suggest-neighbors» за path-параметр region_code и роут будет недостижим.
@router.get("/suggest-neighbors", response_model=SuggestNeighborsResponse)
async def suggest_neighbors(
    code: Optional[str] = None,
    label: Optional[str] = None,
    kind: str = "raion",
    max_km: float = 90.0,
    db: AsyncSession = Depends(get_db_session),
):
    """Подсказать соседей региона по гео-близости центров (OSM-координаты).

    Два режима:
      * ``code`` — существующий регион: координаты берём из ``config['geo']``
        (геокодим лениво, если ещё нет), ``kind`` — из самого региона;
      * ``label`` + ``kind`` — регион ещё не создан (add-модалка): геокодим лейбл.

    Чужие регионы в реквесте **не** геокодим (rate-limit Nominatim ≤1 req/s) —
    берём только закэшированные ``config['geo']``. Не закэшированные возвращаем в
    ``not_geocoded`` — их заполняет ``scripts/backfill_region_geo.py``. Связь
    делается обоюдной при сохранении (см. ``_sync_bidirectional_neighbors``);
    этот endpoint только подсказывает, ничего не применяя.
    """
    self_code: Optional[str] = None
    target_kind = kind
    target_label = label
    target_coords: Optional[Tuple[float, float]] = None

    if code:
        result = await db.execute(select(Region).where(Region.code == code))
        region = result.scalar_one_or_none()
        if not region:
            raise HTTPException(status_code=404, detail="Region not found")
        self_code = region.code
        target_kind = region.kind
        geo = await _ensure_region_coords(db, region)
        if geo:
            target_coords = (geo["lat"], geo["lon"])
            target_label = geo.get("label")
    elif label:
        target_label = _geocodable_label(label) or label
        target_coords = await geocode(target_label)
    else:
        raise HTTPException(status_code=400, detail="Either 'code' or 'label' is required")

    if not target_coords:
        return {
            "target": {"label": target_label, "lat": None, "lon": None, "geocoded": False},
            "suggestions": [],
            "not_geocoded": [],
        }

    # Кандидаты — регионы того же kind (raion↔raion), кроме себя и служебного 'test'.
    # По parent_region_id НЕ фильтруем: трансграничные соседи (Татарстан↔Киров)
    # реальны, и расстояние центров их ловит.
    result = await db.execute(select(Region).where(Region.kind == target_kind))
    candidates = result.scalars().all()

    suggestions: List[Dict[str, Any]] = []
    not_geocoded: List[str] = []
    for r in candidates:
        if r.code == self_code or r.code == "test":
            continue
        cfg = r.config if isinstance(r.config, dict) else {}
        geo = cfg.get("geo")
        if not (isinstance(geo, dict) and "lat" in geo and "lon" in geo):
            not_geocoded.append(r.code)
            continue
        dist = haversine_km(target_coords, (geo["lat"], geo["lon"]))
        suggestions.append(
            {
                "code": r.code,
                "name": r.name,
                "distance_km": round(dist, 1),
                "within_threshold": dist <= max_km,
            }
        )
    suggestions.sort(key=lambda s: s["distance_km"])

    return {
        "target": {
            "label": target_label,
            "lat": target_coords[0],
            "lon": target_coords[1],
            "geocoded": True,
        },
        "suggestions": suggestions,
        "not_geocoded": sorted(not_geocoded),
    }


@router.get("/{region_code}", response_model=RegionResponse)
@cache(ttl=600, key_prefix="regions")  # Cache for 10 minutes
async def get_region(region_code: str, db: AsyncSession = Depends(get_db_session)):
    """Get region by code"""
    result = await db.execute(select(Region).where(Region.code == region_code))
    region = result.scalar_one_or_none()

    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    # Get counts
    comm_count = await db.execute(
        select(func.count(Community.id)).where(Community.region_id == region.id)
    )
    posts_count = await db.execute(select(func.count(Post.id)).where(Post.region_id == region.id))
    pending_count = await db.execute(
        select(func.count(CommunityCandidate.id)).where(
            CommunityCandidate.region_id == region.id,
            CommunityCandidate.status == "pending",
        )
    )

    cfg = region.config if isinstance(region.config, dict) else {}
    loc_list = cfg.get("localities") or []

    return {
        "id": region.id,
        "code": region.code,
        "name": region.name,
        "vk_group_id": region.vk_group_id,
        "telegram_channel": region.telegram_channel,
        "neighbors": region.neighbors,
        "is_active": region.is_active,
        "created_at": region.created_at.isoformat() if region.created_at else "",
        "communities_count": comm_count.scalar() or 0,
        "posts_count": posts_count.scalar() or 0,
        "last_discovery_at": (
            region.last_discovery_at.isoformat() if region.last_discovery_at else None
        ),
        "pending_candidates_count": pending_count.scalar() or 0,
        "has_discovery_config": bool(loc_list and region.center_city),
        "kind": getattr(region, "kind", None) or "raion",
        "parent_region_id": getattr(region, "parent_region_id", None),
    }


@router.post("/", response_model=RegionResponse, status_code=201)
async def create_region(region_data: RegionCreate, db: AsyncSession = Depends(get_db_session)):
    """Create new region"""
    # Check if region with this code already exists
    existing = await db.execute(select(Region).where(Region.code == region_data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400, detail=f"Region with code '{region_data.code}' already exists"
        )

    # Соседи: нормализуем в коды регионов (UI отдаёт коды, но API защищаем от
    # русских названий) и делаем связь обоюдной (см. _sync_bidirectional_neighbors).
    neighbor_codes = await _normalize_neighbor_codes(db, region_data.neighbors, region_data.code)

    # Создание record — `kind` валидируется pydantic-pattern на raion/oblast/strana,
    # `parent_region_id` принимаем без доп. проверки целостности (FK на уровне БД).
    new_region = Region(
        code=region_data.code,
        name=region_data.name,
        vk_group_id=region_data.vk_group_id,
        telegram_channel=region_data.telegram_channel,
        neighbors=(",".join(neighbor_codes) or None),
        local_hashtags=region_data.local_hashtags,
        is_active=region_data.is_active,
        vk_city_id=region_data.vk_city_id,
        center_city=region_data.center_city,
        kind=region_data.kind,
        parent_region_id=region_data.parent_region_id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )

    db.add(new_region)
    await _sync_bidirectional_neighbors(db, region_data.code, None, neighbor_codes)
    await db.commit()
    await db.refresh(new_region)

    # Invalidate regions cache
    await invalidate_cache("regions:*")

    return {
        "id": new_region.id,
        "code": new_region.code,
        "name": new_region.name,
        "vk_group_id": new_region.vk_group_id,
        "telegram_channel": new_region.telegram_channel,
        "neighbors": new_region.neighbors,
        "is_active": new_region.is_active,
        "created_at": new_region.created_at.isoformat(),
        "communities_count": 0,
        "posts_count": 0,
        "kind": new_region.kind,
        "parent_region_id": new_region.parent_region_id,
    }


@router.put("/{region_id}", response_model=RegionResponse)
async def update_region(
    region_id: int, region_data: RegionUpdate, db: AsyncSession = Depends(get_db_session)
):
    """Update existing region"""
    # Get region
    result = await db.execute(select(Region).where(Region.id == region_id))
    region = result.scalar_one_or_none()

    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    # Update fields
    update_data = region_data.dict(exclude_unset=True)

    # Соседи обрабатываем отдельно: нормализуем в коды регионов + делаем связь
    # обоюдной (если A добавил B в соседи — B получает A, и наоборот при удалении).
    neighbors_in_update = "neighbors" in update_data
    new_neighbor_codes: List[str] = []
    old_csv = region.neighbors
    if neighbors_in_update:
        new_neighbor_codes = await _normalize_neighbor_codes(
            db, update_data.pop("neighbors"), region.code
        )

    for field, value in update_data.items():
        setattr(region, field, value)

    if neighbors_in_update:
        region.neighbors = ",".join(new_neighbor_codes) or None
        await _sync_bidirectional_neighbors(db, region.code, old_csv, new_neighbor_codes)

    region.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(region)

    # Invalidate regions cache
    await invalidate_cache("regions:*")

    # Get counts
    comm_count = await db.execute(
        select(func.count(Community.id)).where(Community.region_id == region.id)
    )
    posts_count = await db.execute(select(func.count(Post.id)).where(Post.region_id == region.id))

    return {
        "id": region.id,
        "code": region.code,
        "name": region.name,
        "vk_group_id": region.vk_group_id,
        "telegram_channel": region.telegram_channel,
        "neighbors": region.neighbors,
        "is_active": region.is_active,
        "created_at": region.created_at.isoformat(),
        "communities_count": comm_count.scalar() or 0,
        "posts_count": posts_count.scalar() or 0,
        "kind": region.kind,
        "parent_region_id": region.parent_region_id,
    }


@router.patch("/{region_id}/toggle-status")
async def toggle_region_status(region_id: int, db: AsyncSession = Depends(get_db_session)):
    """Toggle region active status (pause/resume)"""
    # Get region
    result = await db.execute(select(Region).where(Region.id == region_id))
    region = result.scalar_one_or_none()

    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    # Toggle status
    region.is_active = not region.is_active
    region.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(region)

    # Invalidate regions cache
    await invalidate_cache("regions:*")

    status_text = "активирован" if region.is_active else "поставлен на паузу"

    return {
        "success": True,
        "message": f"Регион '{region.name}' {status_text}",
        "is_active": region.is_active,
        "region_id": region.id,
    }


@router.delete("/{region_id}")
async def delete_region(region_id: int, db: AsyncSession = Depends(get_db_session)):
    """Delete region (and all related data)"""
    # Get region
    result = await db.execute(select(Region).where(Region.id == region_id))
    region = result.scalar_one_or_none()

    if not region:
        raise HTTPException(status_code=404, detail="Region not found")

    # Delete related posts first
    await db.execute(delete(Post).where(Post.region_id == region_id))

    # Delete related communities
    await db.execute(delete(Community).where(Community.region_id == region_id))

    # Delete region
    await db.execute(delete(Region).where(Region.id == region_id))

    await db.commit()

    # Invalidate regions cache
    await invalidate_cache("regions:*")

    return {"message": f"Region '{region.name}' deleted successfully"}
