"""
Regions API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List
from pydantic import BaseModel

from database.connection import get_db_session
from database.models import Region

router = APIRouter()


class RegionResponse(BaseModel):
    """Region response model"""
    id: int
    code: str
    name: str
    vk_group_id: int | None
    telegram_channel: str | None
    neighbors: str | None
    is_active: bool
    
    class Config:
        from_attributes = True


@router.get("/", response_model=List[RegionResponse])
async def get_regions(
    skip: int = 0,
    limit: int = 100,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all regions"""
    result = await db.execute(
        select(Region).offset(skip).limit(limit)
    )
    regions = result.scalars().all()
    return regions


@router.get("/{region_code}", response_model=RegionResponse)
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
    
    return region

