"""
Posts API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from typing import List
from pydantic import BaseModel
from datetime import datetime

from database.connection import get_db_session
from database.models import Post

router = APIRouter()


class PostResponse(BaseModel):
    """Post response model"""
    id: int
    region_id: int
    community_id: int
    vk_post_id: int
    vk_owner_id: int
    text: str | None
    date_published: datetime
    views: int
    likes: int
    reposts: int
    comments: int
    ai_category: str | None
    ai_relevance: int | None
    ai_score: int | None
    status: str
    
    class Config:
        from_attributes = True


@router.get("/", response_model=List[PostResponse])
async def get_posts(
    region_id: int | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all posts"""
    query = select(Post).order_by(desc(Post.date_published))
    
    if region_id:
        query = query.where(Post.region_id == region_id)
    if status:
        query = query.where(Post.status == status)
    
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    posts = result.scalars().all()
    
    return posts


@router.get("/{post_id}", response_model=PostResponse)
async def get_post(
    post_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """Get post by ID"""
    result = await db.execute(
        select(Post).where(Post.id == post_id)
    )
    post = result.scalar_one_or_none()
    
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    
    return post

