"""
Regions API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, delete
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
from datetime import datetime

from database.connection import get_db_session
from database.models import Region, Community, Post
from utils.cache import cache, invalidate_cache
from modules.digest_template import (
    STANDARD_TOPICS,
    compute_effective_digest_settings,
    topic_to_default_hashtag,
)

router = APIRouter()


class RegionCreate(BaseModel):
    """Region create model"""
    code: str = Field(..., min_length=2, max_length=50, description="Уникальный код региона (например, 'mi', 'nolinsk')")
    name: str = Field(..., min_length=2, max_length=200, description="Название региона")
    vk_group_id: Optional[int] = Field(None, description="ID главной VK группы (отрицательное число)")
    telegram_channel: Optional[str] = Field(None, max_length=100, description="Telegram канал (например, @malmig_info)")
    neighbors: Optional[str] = Field(None, max_length=500, description="Соседние регионы через запятую")
    local_hashtags: Optional[str] = Field(None, description="Локальные хештеги")
    is_active: bool = Field(True, description="Активен ли регион")


class RegionUpdate(BaseModel):
    """Region update model"""
    name: Optional[str] = Field(None, min_length=2, max_length=200)
    vk_group_id: Optional[int] = None
    telegram_channel: Optional[str] = Field(None, max_length=100)
    neighbors: Optional[str] = Field(None, max_length=500)
    local_hashtags: Optional[str] = None
    is_active: Optional[bool] = None


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
    
    class Config:
        from_attributes = True


class DigestTemplateSettingsModel(BaseModel):
    title: str = Field(..., description="Заголовок дайджеста")
    footer: str = Field("", description="Подвал дайджеста")
    include_source_links: bool = Field(True, description="Показывать кликабельный источник под новостью")
    include_topic_hashtag: bool = Field(True, description="Добавлять хештег темы в конце")
    include_region_hashtags: bool = Field(False, description="Добавлять локальные хештеги региона в конце")
    topic_hashtag_override: str = Field("", description="Переопределение хештега темы (если пусто — берём дефолт)")


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
    digest_template: Dict[str, Any] = cfg.get("digest_template") if isinstance(cfg.get("digest_template"), dict) else {}

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
    dt: Dict[str, Any] = cfg.get("digest_template") if isinstance(cfg.get("digest_template"), dict) else {}
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


@router.get("/", response_model=List[RegionResponse])
@cache(ttl=600, key_prefix="regions")  # Cache for 10 minutes
async def get_regions(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all regions with optimized counts"""
    # Get all regions
    result = await db.execute(
        select(Region).offset(skip).limit(limit)
    )
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
    
    # Build response
    regions_with_counts = []
    for region in regions:
        region_dict = {
            "id": region.id,
            "code": region.code,
            "name": region.name,
            "vk_group_id": region.vk_group_id,
            "telegram_channel": region.telegram_channel,
            "neighbors": region.neighbors,
            "is_active": region.is_active,
            "created_at": region.created_at.isoformat() if hasattr(region, 'created_at') and region.created_at else "",
            "communities_count": comm_counts.get(region.id, 0),
            "posts_count": posts_counts.get(region.id, 0)
        }
        regions_with_counts.append(region_dict)
    
    return regions_with_counts


@router.get("/{region_code}", response_model=RegionResponse)
@cache(ttl=600, key_prefix="regions")  # Cache for 10 minutes
async def get_region(
    region_code: str,
    db: AsyncSession = Depends(get_db_session)
):
    """Get region by code"""
    result = await db.execute(
        select(Region).where(Region.code == region_code)
    )
    region = result.scalar_one_or_none()
    
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    
    # Get counts
    comm_count = await db.execute(
        select(func.count(Community.id)).where(Community.region_id == region.id)
    )
    posts_count = await db.execute(
        select(func.count(Post.id)).where(Post.region_id == region.id)
    )
    
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
        "posts_count": posts_count.scalar() or 0
    }


@router.post("/", response_model=RegionResponse, status_code=201)
async def create_region(
    region_data: RegionCreate,
    db: AsyncSession = Depends(get_db_session)
):
    """Create new region"""
    # Check if region with this code already exists
    existing = await db.execute(
        select(Region).where(Region.code == region_data.code)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"Region with code '{region_data.code}' already exists")
    
    # Create new region
    new_region = Region(
        code=region_data.code,
        name=region_data.name,
        vk_group_id=region_data.vk_group_id,
        telegram_channel=region_data.telegram_channel,
        neighbors=region_data.neighbors,
        local_hashtags=region_data.local_hashtags,
        is_active=region_data.is_active,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    
    db.add(new_region)
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
        "posts_count": 0
    }


@router.put("/{region_id}", response_model=RegionResponse)
async def update_region(
    region_id: int,
    region_data: RegionUpdate,
    db: AsyncSession = Depends(get_db_session)
):
    """Update existing region"""
    # Get region
    result = await db.execute(
        select(Region).where(Region.id == region_id)
    )
    region = result.scalar_one_or_none()
    
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    
    # Update fields
    update_data = region_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(region, field, value)
    
    region.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(region)
    
    # Invalidate regions cache
    await invalidate_cache("regions:*")
    
    # Get counts
    comm_count = await db.execute(
        select(func.count(Community.id)).where(Community.region_id == region.id)
    )
    posts_count = await db.execute(
        select(func.count(Post.id)).where(Post.region_id == region.id)
    )
    
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
        "posts_count": posts_count.scalar() or 0
    }


@router.patch("/{region_id}/toggle-status")
async def toggle_region_status(
    region_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Toggle region active status (pause/resume)"""
    # Get region
    result = await db.execute(
        select(Region).where(Region.id == region_id)
    )
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
        "region_id": region.id
    }


@router.delete("/{region_id}")
async def delete_region(
    region_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Delete region (and all related data)"""
    # Get region
    result = await db.execute(
        select(Region).where(Region.id == region_id)
    )
    region = result.scalar_one_or_none()
    
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    
    # Delete related posts first
    await db.execute(
        delete(Post).where(Post.region_id == region_id)
    )
    
    # Delete related communities
    await db.execute(
        delete(Community).where(Community.region_id == region_id)
    )
    
    # Delete region
    await db.execute(
        delete(Region).where(Region.id == region_id)
    )
    
    await db.commit()
    
    # Invalidate regions cache
    await invalidate_cache("regions:*")
    
    return {"message": f"Region '{region.name}' deleted successfully"}

