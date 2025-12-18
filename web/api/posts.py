"""
Posts API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import joinedload
from typing import List
from pydantic import BaseModel
from datetime import datetime

from database.connection import get_db_session
from database.models import Post, Region, Community
from utils.cache import cache

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
    created_at: datetime
    region_code: str | None = None
    source_url: str | None = None
    category: str | None = None
    media_urls: List[str] = []
    
    class Config:
        from_attributes = True


@router.get("/", response_model=List[PostResponse])
@cache(ttl=120, key_prefix="posts")  # Cache for 2 minutes (posts change frequently)
async def get_posts(
    region_id: int | None = None,
    status: str | None = None,
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    """Get all posts with optimized queries"""
    query = select(Post).order_by(desc(Post.date_published))
    
    if region_id:
        query = query.where(Post.region_id == region_id)
    if status:
        query = query.where(Post.status == status)
    
    query = query.offset(skip).limit(limit)
    result = await db.execute(query)
    posts = result.scalars().all()
    
    # Get all data in bulk (optimized queries)
    region_ids = list(set([p.region_id for p in posts]))
    community_ids = list(set([p.community_id for p in posts]))
    
    # Get all region codes at once
    regions_result = await db.execute(
        select(Region.id, Region.code).where(Region.id.in_(region_ids))
    )
    region_codes = {row[0]: row[1] for row in regions_result.all()}
    
    # Get all community categories at once
    communities_result = await db.execute(
        select(Community.id, Community.category).where(Community.id.in_(community_ids))
    )
    community_categories = {row[0]: row[1] for row in communities_result.all()}
    
    # Build response
    enriched_posts = []
    for post in posts:
        source_url = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}"
        
        post_dict = {
            "id": post.id,
            "region_id": post.region_id,
            "community_id": post.community_id,
            "vk_post_id": post.vk_post_id,
            "vk_owner_id": post.vk_owner_id,
            "text": post.text,
            "date_published": post.date_published,
            "views": post.views,
            "likes": post.likes,
            "reposts": post.reposts,
            "comments": post.comments,
            "ai_category": post.ai_category,
            "ai_relevance": post.ai_relevance,
            "ai_score": post.ai_score,
            "status": post.status,
            "created_at": post.created_at if hasattr(post, 'created_at') else post.date_published,
            "region_code": region_codes.get(post.region_id),
            "source_url": source_url,
            "category": community_categories.get(post.community_id) or post.ai_category,
            "media_urls": []  # TODO: Parse media from post data
        }
        enriched_posts.append(post_dict)
    
    return enriched_posts


@router.get("/{post_id}", response_model=PostResponse)
@cache(ttl=180, key_prefix="posts")  # Cache for 3 minutes
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
    
    # Get region code
    region_result = await db.execute(
        select(Region.code).where(Region.id == post.region_id)
    )
    region_code = region_result.scalar()
    
    # Get community category
    comm_result = await db.execute(
        select(Community.category).where(Community.id == post.community_id)
    )
    community_category = comm_result.scalar()
    
    # Build source URL
    source_url = f"https://vk.com/wall{post.vk_owner_id}_{post.vk_post_id}"
    
    post_dict = {
        "id": post.id,
        "region_id": post.region_id,
        "community_id": post.community_id,
        "vk_post_id": post.vk_post_id,
        "vk_owner_id": post.vk_owner_id,
        "text": post.text,
        "date_published": post.date_published,
        "views": post.views,
        "likes": post.likes,
        "reposts": post.reposts,
        "comments": post.comments,
        "ai_category": post.ai_category,
        "ai_relevance": post.ai_relevance,
        "ai_score": post.ai_score,
        "status": post.status,
        "created_at": post.created_at if hasattr(post, 'created_at') else post.date_published,
        "region_code": region_code,
        "source_url": source_url,
        "category": community_category or post.ai_category,
        "media_urls": []  # TODO: Parse media from post data
    }
    
    return post_dict

