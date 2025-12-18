"""
Workflow API endpoints - manage automated content pipeline
"""
from fastapi import APIRouter, HTTPException, BackgroundTasks
from typing import Optional, List
from pydantic import BaseModel

from modules.scheduler.scheduler import ContentScheduler
from modules.publisher.publisher import ContentPublisher
from config.config_secure import VK_TOKENS, TELEGRAM_TOKENS, GROQ_API_KEY

router = APIRouter(prefix="/api/workflow", tags=["workflow"])


# Request/Response models
class PublishRequest(BaseModel):
    post_id: int
    platforms: List[str]
    region_code: str


class CycleRequest(BaseModel):
    region_code: Optional[str] = None


# Global instances (will be initialized on startup)
_publisher = None
_scheduler = None


def get_publisher() -> ContentPublisher:
    """Get or initialize publisher"""
    global _publisher
    if _publisher is None:
        vk_token = next((token for token in VK_TOKENS.values() if token), None)
        telegram_token = TELEGRAM_TOKENS.get("AFONYA")
        
        _publisher = ContentPublisher(
            vk_token=vk_token,
            telegram_token=telegram_token
        )
    return _publisher


def get_scheduler() -> ContentScheduler:
    """Get or initialize scheduler"""
    global _scheduler
    if _scheduler is None:
        tokens = [token for token in VK_TOKENS.values() if token]
        publisher = get_publisher()
        
        _scheduler = ContentScheduler(
            vk_tokens=tokens,
            groq_api_key=GROQ_API_KEY,
            publisher=publisher
        )
    return _scheduler


@router.get("/status")
async def get_workflow_status():
    """
    Get current workflow status
    
    Returns:
        Pipeline statistics and status
    """
    try:
        scheduler = get_scheduler()
        stats = await scheduler.get_pipeline_stats()
        
        return {
            "status": "ok",
            "pipeline": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/run-cycle")
async def run_full_cycle(
    request: CycleRequest,
    background_tasks: BackgroundTasks
):
    """
    Run full content cycle: Monitor → Analyze → Publish
    
    Args:
        request: Cycle configuration
        
    Returns:
        Cycle results
    """
    try:
        scheduler = get_scheduler()
        result = await scheduler.run_full_cycle(request.region_code)
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/publish")
async def publish_post(request: PublishRequest):
    """
    Publish specific post to platforms
    
    Args:
        request: Publishing request
        
    Returns:
        Publishing results
    """
    try:
        publisher = get_publisher()
        result = await publisher.publish_post(
            post_id=request.post_id,
            platforms=request.platforms,
            region_code=request.region_code
        )
        
        if result.get('error'):
            raise HTTPException(status_code=400, detail=result['error'])
        
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/publishers/status")
async def check_publishers():
    """
    Check all publisher connections
    
    Returns:
        Connection status for each platform
    """
    try:
        publisher = get_publisher()
        connections = await publisher.check_all_connections()
        
        return {
            "status": "ok",
            "platforms": connections
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schedule")
async def get_schedule():
    """
    Get publishing schedule for all regions
    
    Returns:
        Schedule information
    """
    try:
        scheduler = get_scheduler()
        schedule = await scheduler.get_schedule_status()
        
        return schedule
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/stats")
async def get_publishing_stats():
    """
    Get publishing statistics
    
    Returns:
        Publishing statistics
    """
    try:
        publisher = get_publisher()
        stats = await publisher.get_publishing_stats()
        
        return {
            "status": "ok",
            "stats": stats
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

