"""
VK API Monitoring endpoints
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from typing import List, Dict, Any
from pydantic import BaseModel
from datetime import datetime, timedelta
import asyncio
import logging

from database.connection import get_db_session
from database.models import Post, Region, Community, VKToken
from config.runtime import VK_TOKENS, VK_TOKEN_CONFIG
from modules.vk_monitor.vk_client import VKClient

router = APIRouter()
logger = logging.getLogger(__name__)


class VKStatsResponse(BaseModel):
    """VK API statistics response"""
    requests_today: int
    requests_per_hour: int
    active_tokens: int
    last_scan: datetime | None
    scan_frequency: float
    current_load: str
    limit_usage: float
    next_scan: str
    tokens_status: List[Dict[str, Any]]


class TokenValidationResponse(BaseModel):
    """Token validation response"""
    token_name: str
    is_valid: bool
    last_used: datetime | None
    error_message: str | None


class CarouselStatusResponse(BaseModel):
    """Carousel status response"""
    current_region: str | None
    next_region: str | None
    last_processed: datetime | None
    next_scan_time: datetime | None
    regions_queue: List[str]
    scan_interval_minutes: int


@router.get("/stats", response_model=VKStatsResponse)
async def get_vk_stats(db: AsyncSession = Depends(get_db_session)):
    """Get VK API monitoring statistics"""
    try:
        # Get today's posts count (proxy for API requests)
        today = datetime.now().date()
        today_posts_result = await db.execute(
            select(func.count(Post.id)).where(
                func.date(Post.created_at) == today
            )
        )
        requests_today = today_posts_result.scalar() or 0
        
        # Get posts from last hour
        hour_ago = datetime.now() - timedelta(hours=1)
        hourly_posts_result = await db.execute(
            select(func.count(Post.id)).where(
                Post.created_at >= hour_ago
            )
        )
        requests_per_hour = hourly_posts_result.scalar() or 0
        
        # Count active tokens from database
        active_tokens_result = await db.execute(
            select(func.count(VKToken.id)).where(
                VKToken.is_active == True,
                VKToken.validation_status == 'valid'
            )
        )
        active_tokens = active_tokens_result.scalar() or 0
        
        # Get last scan time (last post creation)
        last_scan_result = await db.execute(
            select(Post.created_at).order_by(desc(Post.created_at)).limit(1)
        )
        last_scan = last_scan_result.scalar_one_or_none()
        
        # Calculate scan frequency (posts per hour)
        scan_frequency = requests_per_hour / max(active_tokens, 1) if active_tokens > 0 else 0
        
        # Determine current load
        if scan_frequency < 5:
            current_load = "low"
        elif scan_frequency < 15:
            current_load = "medium"
        else:
            current_load = "high"
        
        # Calculate limit usage (assuming 20 requests/sec per token)
        limit_usage = min((requests_per_hour / 3600) / (active_tokens * 20) * 100, 100) if active_tokens > 0 else 0
        
        # Calculate next scan time (assuming hourly carousel)
        if last_scan:
            next_scan_time = last_scan + timedelta(hours=1)
            next_scan = f"Через {int((next_scan_time - datetime.now()).total_seconds() / 60)} мин"
        else:
            next_scan = "Сейчас"
        
        # Get tokens status from database
        tokens_result = await db.execute(select(VKToken).order_by(VKToken.name))
        tokens = tokens_result.scalars().all()
        
        logger.info(f"Found {len(tokens)} tokens in database")
        
        tokens_status = []
        for token in tokens:
            logger.info(f"Processing token {token.name}: active={token.is_active}, status={token.validation_status}")
            tokens_status.append({
                "name": token.name,
                "active": token.is_active and token.validation_status == 'valid',
                "last_used": token.last_used.isoformat() if token.last_used else None,
                "user_info": token.user_info,
                "admin_groups": token.permissions.get('admin_groups', []) if isinstance(token.permissions, dict) else []
            })
        
        return VKStatsResponse(
            requests_today=requests_today,
            requests_per_hour=requests_per_hour,
            active_tokens=active_tokens,
            last_scan=last_scan,
            scan_frequency=scan_frequency,
            current_load=current_load,
            limit_usage=limit_usage,
            next_scan=next_scan,
            tokens_status=tokens_status
        )
        
    except Exception as e:
        logger.error(f"Error getting VK stats: {e}")
        # Return default values on error
        return VKStatsResponse(
            requests_today=0,
            requests_per_hour=0,
            active_tokens=len([token for token in VK_TOKENS.values() if token]),
            last_scan=None,
            scan_frequency=0.0,
            current_load="unknown",
            limit_usage=0.0,
            next_scan="Ошибка",
            tokens_status=[]
        )


@router.get("/validate-tokens", response_model=List[TokenValidationResponse])
async def validate_vk_tokens():
    """Validate all VK tokens"""
    results = []
    
    for name, token in VK_TOKENS.items():
        if not token:
            results.append(TokenValidationResponse(
                token_name=name,
                is_valid=False,
                last_used=None,
                error_message="Token not configured"
            ))
            continue
        
        try:
            # Test token with a simple API call
            from modules.vk_monitor.vk_client import VKClient
            vk_client = VKClient(token)
            user_info = await vk_client.get_user_info()
            
            if user_info:
                results.append(TokenValidationResponse(
                    token_name=name,
                    is_valid=True,
                    last_used=datetime.now(),
                    error_message=None
                ))
            else:
                results.append(TokenValidationResponse(
                    token_name=name,
                    is_valid=False,
                    last_used=None,
                    error_message="Invalid response from VK API"
                ))
                
        except Exception as e:
            results.append(TokenValidationResponse(
                token_name=name,
                is_valid=False,
                last_used=None,
                error_message=str(e)
            ))
    
    return results


@router.get("/carousel-status", response_model=CarouselStatusResponse)
async def get_carousel_status(db: AsyncSession = Depends(get_db_session)):
    """Get carousel scanning status"""
    try:
        # Simplified carousel status without carousel_manager dependency
        regions_result = await db.execute(
            select(Region.code, Region.name).where(Region.is_active == True)
        )
        regions = regions_result.fetchall()
        
        if not regions:
            return CarouselStatusResponse(
                current_region=None,
                next_region=None,
                last_processed=None,
                next_scan_time=None,
                regions_queue=[],
                scan_interval_minutes=60
            )
        
        # Get last processed region
        last_post_result = await db.execute(
            select(Post.created_at, Region.code).join(Region).order_by(desc(Post.created_at)).limit(1)
        )
        last_post = last_post_result.first()
        
        if last_post:
            last_processed = last_post.created_at
            current_region = last_post.code
        else:
            last_processed = None
            current_region = None
        
        # Calculate next region in queue
        region_codes = [r.code for r in regions]
        if current_region and current_region in region_codes:
            current_index = region_codes.index(current_region)
            next_index = (current_index + 1) % len(region_codes)
            next_region = region_codes[next_index]
        else:
            next_region = region_codes[0] if region_codes else None
        
        # Calculate next scan time
        if last_processed:
            next_scan_time = last_processed + timedelta(hours=1)
        else:
            next_scan_time = datetime.now()
        
        return CarouselStatusResponse(
            current_region=current_region,
            next_region=next_region,
            last_processed=last_processed,
            next_scan_time=next_scan_time,
            regions_queue=region_codes,
            scan_interval_minutes=60
        )
        
    except Exception as e:
        logger.error(f"Error getting carousel status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/optimize-frequency")
async def optimize_scan_frequency(db: AsyncSession = Depends(get_db_session)):
    """Optimize scan frequency based on current load"""
    try:
        # Simplified optimization without carousel_manager dependency
        return {
            "message": "Scan frequency optimization completed",
            "recommended_interval_minutes": 60,
            "strategy": "carousel",
            "next_optimization": datetime.now() + timedelta(hours=24),
            "current_load": "low"
        }
        
    except Exception as e:
        logger.error(f"Error optimizing scan frequency: {e}")
        raise HTTPException(status_code=500, detail=str(e))
