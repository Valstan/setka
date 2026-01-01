"""
VK Publisher API endpoints

Предоставляет REST API для публикации дайджестов в VK группы
"""
import logging
from typing import List, Dict, Any, Optional
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import get_db_session
from database.models import Post, Region, Community
from modules.publisher.vk_publisher import VKPublisher
from modules.aggregation.aggregator import NewsAggregator
from sqlalchemy import select, and_
from datetime import datetime, timedelta
from config.runtime import VK_MAIN_TOKENS, VK_TEST_GROUP_ID, VK_PRODUCTION_GROUPS

logger = logging.getLogger(__name__)

router = APIRouter()


# Pydantic models
class PublishRequest(BaseModel):
    """Запрос на публикацию"""
    region_code: str = Field(..., description="Код региона (mi, nolinsk, etc)")
    max_posts: int = Field(5, ge=1, le=10, description="Максимальное количество постов в дайджесте")
    publish_mode: str = Field("test", description="Режим публикации: test или production")
    custom_text: Optional[str] = Field(None, description="Кастомный текст для поста")
    hashtags: Optional[List[str]] = Field(None, description="Дополнительные хештеги")


class PublishResponse(BaseModel):
    """Ответ на запрос публикации"""
    success: bool
    message: str
    post_id: Optional[int] = None
    post_url: Optional[str] = None
    group_id: Optional[int] = None
    error: Optional[str] = None
    digest_info: Optional[Dict[str, Any]] = None


class SimplePublishRequest(BaseModel):
    """Простой запрос на публикацию текста"""
    text: str = Field(..., description="Текст для публикации")
    group_id: Optional[int] = Field(None, description="ID группы (если не указан, используется тестовая)")
    from_group: bool = Field(True, description="Публиковать от имени группы")


class GroupInfo(BaseModel):
    """Информация о группе VK"""
    id: int
    name: str
    screen_name: str
    type: str
    url: str


# Helper functions
async def get_vk_publisher() -> VKPublisher:
    """Получить VK Publisher с токеном"""
    try:
        token = VK_MAIN_TOKENS["VALSTAN"]["token"]
        return VKPublisher(token)
    except Exception as e:
        logger.error(f"Failed to initialize VK Publisher: {e}")
        raise HTTPException(status_code=500, detail="VK Publisher initialization failed")


async def get_posts_for_region(
    session: AsyncSession,
    region_code: str,
    max_posts: int = 5
) -> List[Post]:
    """Получить посты для региона"""
    try:
        # Получаем регион
        result = await session.execute(
            select(Region).where(Region.code == region_code)
        )
        region = result.scalar_one_or_none()
        
        if not region:
            raise HTTPException(status_code=404, detail=f"Region {region_code} not found")
        
        # Получаем посты
        result = await session.execute(
            select(Post)
            .join(Community)
            .where(
                and_(
                    Community.region_id == region.id,
                    Post.ai_analyzed == True,
                    Post.status == 'new',
                    Post.date_published >= datetime.now() - timedelta(hours=24)
                )
            )
            .order_by(Post.ai_score.desc())
            .limit(max_posts)
        )
        
        posts = list(result.scalars().all())
        return posts
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get posts for region {region_code}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get posts")


# API Endpoints
@router.get("/groups", response_model=List[GroupInfo])
async def get_available_groups():
    """Получить список доступных групп для публикации"""
    try:
        publisher = await get_vk_publisher()
        
        groups = []
        
        # Тестовая группа
        test_group_info = publisher.get_group_info(VK_TEST_GROUP_ID)
        if test_group_info:
            groups.append(GroupInfo(**test_group_info))
        
        # Production группы
        for region_code, group_id in VK_PRODUCTION_GROUPS.items():
            group_info = publisher.get_group_info(group_id)
            if group_info:
                groups.append(GroupInfo(**group_info))
        
        return groups
        
    except Exception as e:
        logger.error(f"Failed to get groups: {e}")
        raise HTTPException(status_code=500, detail="Failed to get groups")


@router.post("/publish/simple", response_model=PublishResponse)
async def publish_simple_post(request: SimplePublishRequest):
    """Публикация простого текстового поста"""
    try:
        publisher = await get_vk_publisher()
        
        group_id = request.group_id or VK_TEST_GROUP_ID
        
        result = await publisher.publish_digest(
            text=request.text,
            target_group_id=group_id,
            from_group=request.from_group
        )
        
        if result['success']:
            return PublishResponse(
                success=True,
                message="Post published successfully",
                post_id=result['post_id'],
                post_url=result['url'],
                group_id=result['group_id']
            )
        else:
            return PublishResponse(
                success=False,
                message="Failed to publish post",
                error=result['error'],
                group_id=group_id
            )
            
    except Exception as e:
        logger.error(f"Failed to publish simple post: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/publish/region", response_model=PublishResponse)
async def publish_region_digest(
    request: PublishRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db_session)
):
    """Публикация дайджеста для региона"""
    try:
        publisher = await get_vk_publisher()
        
        # Получаем посты для региона
        posts = await get_posts_for_region(session, request.region_code, request.max_posts)
        
        if not posts:
            return PublishResponse(
                success=False,
                message=f"No posts available for region {request.region_code}",
                error="No posts found"
            )
        
        # Определяем целевую группу
        target_group_id = publisher.get_target_group_id(request.region_code, request.publish_mode)
        
        # Создаем дайджест
        aggregator = NewsAggregator(max_posts_per_digest=request.max_posts)
        
        # Получаем регион для заголовка
        result = await session.execute(
            select(Region).where(Region.code == request.region_code)
        )
        region = result.scalar_one_or_none()
        
        title = f"📰 НОВОСТИ {region.name.upper()}" if region else f"📰 НОВОСТИ {request.region_code.upper()}"
        
        hashtags = [f"#Новости{request.region_code.upper()}"]
        if request.hashtags:
            hashtags.extend(request.hashtags)
        
        digest = await aggregator.aggregate(
            posts=posts,
            title=title,
            hashtags=hashtags
        )
        
        if not digest:
            return PublishResponse(
                success=False,
                message="Failed to create digest",
                error="Aggregation failed"
            )
        
        # Публикуем
        result = await publisher.publish_aggregated_post(digest, target_group_id)
        
        if result['success']:
            digest_info = {
                "sources_count": digest.sources_count,
                "total_views": digest.total_views,
                "total_likes": digest.total_likes,
                "total_reposts": digest.total_reposts,
                "categories": digest.categories
            }
            
            return PublishResponse(
                success=True,
                message="Digest published successfully",
                post_id=result['post_id'],
                post_url=result['url'],
                group_id=result['group_id'],
                digest_info=digest_info
            )
        else:
            return PublishResponse(
                success=False,
                message="Failed to publish digest",
                error=result['error'],
                group_id=target_group_id
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to publish region digest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/publish/custom", response_model=PublishResponse)
async def publish_custom_digest(
    request: PublishRequest,
    session: AsyncSession = Depends(get_db_session)
):
    """Публикация кастомного дайджеста"""
    try:
        publisher = await get_vk_publisher()
        
        if not request.custom_text:
            raise HTTPException(status_code=400, detail="custom_text is required")
        
        # Определяем целевую группу
        target_group_id = publisher.get_target_group_id(request.region_code, request.publish_mode)
        
        # Публикуем кастомный текст
        result = await publisher.publish_digest(
            text=request.custom_text,
            target_group_id=target_group_id,
            from_group=True
        )
        
        if result['success']:
            return PublishResponse(
                success=True,
                message="Custom post published successfully",
                post_id=result['post_id'],
                post_url=result['url'],
                group_id=result['group_id']
            )
        else:
            return PublishResponse(
                success=False,
                message="Failed to publish custom post",
                error=result['error'],
                group_id=target_group_id
            )
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to publish custom digest: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/regions/{region_code}/posts")
async def get_region_posts(
    region_code: str,
    max_posts: int = 10,
    session: AsyncSession = Depends(get_db_session)
):
    """Получить посты для региона (для предварительного просмотра)"""
    try:
        posts = await get_posts_for_region(session, region_code, max_posts)
        
        result = []
        for post in posts:
            result.append({
                "id": post.id,
                "text": post.text[:200] + "..." if len(post.text) > 200 else post.text,
                "views": post.views,
                "likes": post.likes,
                "reposts": post.reposts,
                "ai_score": post.ai_score,
                "ai_category": post.ai_category,
                "date_published": post.date_published,
                "community": {
                    "name": post.community.name,
                    "vk_id": post.community.vk_id
                } if post.community else None
            })
        
        return {
            "region_code": region_code,
            "posts_count": len(result),
            "posts": result
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get region posts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_publisher_status():
    """Получить статус VK Publisher"""
    try:
        publisher = await get_vk_publisher()
        
        # Проверяем подключение
        test_group_info = publisher.get_group_info(VK_TEST_GROUP_ID)
        
        return {
            "status": "active" if test_group_info else "inactive",
            "vk_publisher": "initialized",
            "test_group": test_group_info,
            "available_tokens": len(VK_MAIN_TOKENS),
            "production_groups": len(VK_PRODUCTION_GROUPS)
        }
        
    except Exception as e:
        logger.error(f"Failed to get publisher status: {e}")
        return {
            "status": "error",
            "error": str(e)
        }
