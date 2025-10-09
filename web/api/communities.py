"""
Communities API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from database.connection import get_db_session
from database.models import Community

router = APIRouter()


class CommunityResponse(BaseModel):
    """Community response model"""
    id: int
    region_id: int
    vk_id: int
    name: str
    category: str
    is_active: bool
    posts_count: int
    
    class Config:
        from_attributes = True


@router.get("/", response_model=List[CommunityResponse])
async def get_communities(
    region_id: int | None = None,
    category: str | None = None,
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all communities"""
    query = select(Community)
    
    if region_id:
        query = query.where(Community.region_id == region_id)
    if category:
        query = query.where(Community.category == category)
    
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    communities = result.scalars().all()
    
    return communities


@router.get("/{community_id}", response_model=CommunityResponse)
async def get_community(
    community_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Get community by ID"""
    result = await db.execute(
        select(Community).where(Community.id == community_id)
    )
    community = result.scalar_one_or_none()
    
    if not community:
        raise HTTPException(status_code=404, detail="Community not found")
    
    return community

