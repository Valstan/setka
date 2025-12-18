"""
Communities API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete
from typing import List, Optional, Union
from pydantic import BaseModel, Field, validator
from datetime import datetime
import vk_api
import re

from database.connection import get_db_session
from database.models import Community, Region, VKToken
from config.config_secure import VK_TOKENS
from utils.cache import cache, invalidate_cache

router = APIRouter()


def extract_vk_id_from_input(vk_input: str, vk_api_instance) -> int:
    """
    Extract VK group ID from various input formats:
    - URL: https://vk.com/43admmalmyzh43
    - URL with params: https://vk.com/club160597747?search_track_code=...
    - Screen name: 43admmalmyzh43
    - Numeric ID: -123456789 or 123456789
    
    Returns negative group ID for use in VK API
    """
    vk_input = str(vk_input).strip()
    
    # Remove query parameters from URL (everything after ?)
    if '?' in vk_input:
        vk_input = vk_input.split('?')[0]
    
    # Try to parse as integer first (direct ID)
    try:
        numeric_id = int(vk_input)
        # If positive, make it negative (groups convention)
        if numeric_id > 0:
            # Verify group exists
            try:
                group = vk_api_instance.groups.getById(group_id=numeric_id)[0]
                return -numeric_id
            except Exception as e:
                raise ValueError(f"Сообщество с ID {numeric_id} не найдено в VK: {str(e)}")
        else:
            # Already negative, verify it exists
            try:
                group = vk_api_instance.groups.getById(group_id=abs(numeric_id))[0]
                return numeric_id
            except Exception as e:
                raise ValueError(f"Сообщество с ID {numeric_id} не найдено в VK: {str(e)}")
    except ValueError:
        pass  # Not a number, continue with URL/screen_name parsing
    
    # Extract screen_name from URL or use as-is
    screen_name = vk_input
    
    # Parse URL patterns - try to extract ID from club/public prefix first
    url_patterns = [
        # Pattern for club123456 or public123456 (numeric ID after prefix)
        r'(?:https?://)?(?:www\.)?vk\.com/(?:club|public)(\d+)',
        # Pattern for general club/public with alphanumeric
        r'(?:https?://)?(?:www\.)?vk\.com/(?:club|public)([a-zA-Z0-9_]+)',
        # Pattern for any VK URL
        r'(?:https?://)?(?:www\.)?vk\.com/([a-zA-Z0-9_]+)',
    ]
    
    for pattern in url_patterns:
        match = re.search(pattern, vk_input)
        if match:
            screen_name = match.group(1)
            
            # If screen_name is purely numeric (from club/public prefix), convert directly
            if screen_name.isdigit():
                numeric_id = int(screen_name)
                try:
                    group = vk_api_instance.groups.getById(group_id=numeric_id)[0]
                    return -numeric_id
                except Exception as e:
                    raise ValueError(f"Сообщество с ID {numeric_id} не найдено в VK: {str(e)}")
            break
    
    # Remove any remaining URL artifacts
    screen_name = screen_name.replace('/', '').replace('\\', '')
    
    if not screen_name:
        raise ValueError("Не удалось извлечь идентификатор сообщества из введенных данных")
    
    # Use VK API to resolve screen_name
    try:
        result = vk_api_instance.utils.resolveScreenName(screen_name=screen_name)
        
        if not result:
            raise ValueError(f"Не удалось найти объект с адресом '{screen_name}' в VK")
        
        if result['type'] != 'group':
            raise ValueError(f"Объект '{screen_name}' не является сообществом (найден тип: {result['type']})")
        
        # Return negative ID for groups
        return -result['object_id']
        
    except vk_api.exceptions.ApiError as e:
        raise ValueError(f"Ошибка VK API при поиске сообщества '{screen_name}': {str(e)}")
    except Exception as e:
        raise ValueError(f"Не удалось получить информацию о сообществе '{screen_name}': {str(e)}")


class CommunityCreate(BaseModel):
    """Community create model"""
    region_id: int = Field(..., description="ID региона")
    vk_id: Union[int, str] = Field(..., description="VK ID, URL или короткое имя сообщества")
    category: str = Field(..., description="Категория: admin, novost, kultura, sport, reklama и т.д.")
    is_active: bool = Field(True, description="Активно ли сообщество для мониторинга")
    
    @validator('vk_id', pre=True)
    def validate_vk_id(cls, v):
        """Validate and preserve vk_id as string or int"""
        if v is None or (isinstance(v, str) and not v.strip()):
            raise ValueError('VK ID не может быть пустым')
        # Keep as-is, will be processed by extract_vk_id_from_input
        return v
    
    class Config:
        # Allow arbitrary types for Union handling
        arbitrary_types_allowed = True


class CommunityUpdate(BaseModel):
    """Community update model"""
    category: Optional[str] = None
    is_active: Optional[bool] = None


class CommunityResponse(BaseModel):
    """Community response model"""
    id: int
    region_id: int
    region_code: str | None
    region_name: str | None
    vk_id: int
    screen_name: str | None
    name: str
    category: str
    is_active: bool
    last_checked: str | None
    posts_count: int
    created_at: str
    
    class Config:
        from_attributes = True


@router.get("/", response_model=List[CommunityResponse])
@cache(ttl=300, key_prefix="communities")  # Cache for 5 minutes
async def get_all_communities(
    region_id: Optional[int] = None,
    category: Optional[str] = None,
    is_active: Optional[bool] = None,
    limit: int = 1000,
    skip: int = 0,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all communities with optional filters"""
    from sqlalchemy.orm import selectinload
    
    # Join with Region to get region info
    query = select(Community).options(selectinload(Community.region))
    
    # Apply filters
    if region_id is not None:
        query = query.where(Community.region_id == region_id)
    if category is not None:
        query = query.where(Community.category == category)
    if is_active is not None:
        query = query.where(Community.is_active == is_active)
    
    # Apply pagination
    query = query.offset(skip).limit(limit)
    
    result = await db.execute(query.order_by(Community.category, Community.name))
    communities = result.scalars().all()
    
    return [
        {
            "id": c.id,
            "region_id": c.region_id,
            "region_code": c.region.code if c.region else None,
            "region_name": c.region.name if c.region else None,
            "vk_id": c.vk_id,
            "screen_name": c.screen_name,
            "name": c.name,
            "category": c.category,
            "is_active": c.is_active,
            "last_checked": c.last_checked.isoformat() if c.last_checked else None,
            "posts_count": c.posts_count,
            "created_at": c.created_at.isoformat() if c.created_at else ""
        }
        for c in communities
    ]


@router.get("/region/{region_id}", response_model=List[CommunityResponse])
@cache(ttl=300, key_prefix="communities")  # Cache for 5 minutes
async def get_region_communities(
    region_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all communities for a region"""
    from sqlalchemy.orm import selectinload
    
    result = await db.execute(
        select(Community)
        .options(selectinload(Community.region))
        .where(Community.region_id == region_id)
        .order_by(Community.category, Community.name)
    )
    communities = result.scalars().all()
    
    return [
        {
            "id": c.id,
            "region_id": c.region_id,
            "region_code": c.region.code if c.region else None,
            "region_name": c.region.name if c.region else None,
            "vk_id": c.vk_id,
            "screen_name": c.screen_name,
            "name": c.name,
            "category": c.category,
            "is_active": c.is_active,
            "last_checked": c.last_checked.isoformat() if c.last_checked else None,
            "posts_count": c.posts_count,
            "created_at": c.created_at.isoformat() if c.created_at else ""
        }
        for c in communities
    ]


@router.post("/", response_model=CommunityResponse, status_code=201)
async def add_community(
    community_data: CommunityCreate,
    db: AsyncSession = Depends(get_db_session)
):
    """Add community to region"""
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Adding community: region_id={community_data.region_id}, vk_id={community_data.vk_id!r}, category={community_data.category}")
    
    # Check if region exists
    region_result = await db.execute(
        select(Region).where(Region.id == community_data.region_id)
    )
    region = region_result.scalar_one_or_none()
    if not region:
        raise HTTPException(status_code=404, detail="Region not found")
    
    # Get VK token from database
    token_result = await db.execute(
        select(VKToken).where(
            VKToken.name == "VALSTAN",
            VKToken.is_active == True,
            VKToken.validation_status == 'valid'
        )
    )
    vk_token_record = token_result.scalar_one_or_none()
    
    if not vk_token_record:
        raise HTTPException(status_code=500, detail="VK token VALSTAN not found or invalid")
    
    vk_token = vk_token_record.get_full_token()
    
    try:
        vk_session = vk_api.VkApi(token=vk_token)
        vk = vk_session.get_api()
        
        # Extract VK ID from input (URL, screen_name, or numeric ID)
        try:
            vk_id = extract_vk_id_from_input(community_data.vk_id, vk)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        
        # Check if community already exists
        existing = await db.execute(
            select(Community).where(
                Community.vk_id == vk_id,
                Community.region_id == community_data.region_id
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Community already exists in this region")
        
        # Get group info
        group_id = abs(vk_id)
        groups = vk.groups.getById(group_id=group_id)
        
        if not groups:
            raise HTTPException(status_code=404, detail="VK community not found")
        
        group = groups[0]
        
        # Create community
        new_community = Community(
            region_id=community_data.region_id,
            vk_id=vk_id,
            screen_name=group.get('screen_name', ''),
            name=group.get('name', ''),
            category=community_data.category,
            is_active=community_data.is_active,
            check_interval=300,
            posts_count=0,
            errors_count=0,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        
        db.add(new_community)
        await db.commit()
        await db.refresh(new_community)
        
        # Invalidate communities cache
        await invalidate_cache("communities:*")
        
        return {
            "id": new_community.id,
            "region_id": new_community.region_id,
            "region_code": region.code if region else None,
            "region_name": region.name if region else None,
            "vk_id": new_community.vk_id,
            "screen_name": new_community.screen_name,
            "name": new_community.name,
            "category": new_community.category,
            "is_active": new_community.is_active,
            "last_checked": None,
            "posts_count": 0,
            "created_at": new_community.created_at.isoformat()
        }
        
    except vk_api.exceptions.ApiError as e:
        raise HTTPException(status_code=400, detail=f"VK API error: {str(e)}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}")


@router.put("/{community_id}", response_model=CommunityResponse)
async def update_community(
    community_id: int,
    community_data: CommunityUpdate,
    db: AsyncSession = Depends(get_db_session)
):
    """Update community"""
    from sqlalchemy.orm import selectinload
    
    result = await db.execute(
        select(Community)
        .options(selectinload(Community.region))
        .where(Community.id == community_id)
    )
    community = result.scalar_one_or_none()
    
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    
    # Update fields
    update_data = community_data.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(community, field, value)
    
    community.updated_at = datetime.utcnow()
    
    await db.commit()
    await db.refresh(community)
    
    # Invalidate communities cache
    await invalidate_cache("communities:*")
    
    return {
        "id": community.id,
        "region_id": community.region_id,
        "region_code": community.region.code if community.region else None,
        "region_name": community.region.name if community.region else None,
        "vk_id": community.vk_id,
        "screen_name": community.screen_name,
        "name": community.name,
        "category": community.category,
        "is_active": community.is_active,
        "last_checked": community.last_checked.isoformat() if community.last_checked else None,
        "posts_count": community.posts_count,
        "created_at": community.created_at.isoformat()
    }


@router.delete("/{community_id}")
async def delete_community(
    community_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Delete community"""
    result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    community = result.scalar_one_or_none()
    
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    
    await db.execute(
        delete(Community).where(Community.id == community_id)
    )
    
    await db.commit()
    
    # Invalidate communities cache
    await invalidate_cache("communities:*")
    
    return {"message": f"Community '{community.name}' deleted successfully"}
