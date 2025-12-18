"""
Smart Scheduler API endpoints
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional
from datetime import datetime, timedelta
from pydantic import BaseModel

from database.connection import get_db_session
from modules.scheduler.smart_scheduler import SmartScheduler
from utils.cache import cache

router = APIRouter()


class ScheduleRequest(BaseModel):
    """Request to schedule publication"""
    digest_id: int
    region_code: str
    category: str = 'novost'
    scheduled_time: Optional[str] = None  # ISO format


class OptimalTimeResponse(BaseModel):
    """Optimal time response"""
    region_code: str
    category: str
    optimal_hour: int
    optimal_minute: int
    optimal_time: str  # ISO format
    engagement_forecast: float


@router.get("/optimal-time/{region_code}")
@cache(ttl=1800, key_prefix="scheduler")  # Cache for 30 minutes
async def get_optimal_time(
    region_code: str,
    category: str = Query('novost', description="Категория контента"),
    time_slot: Optional[str] = Query(None, description="morning, afternoon, или evening")
):
    """
    Получить оптимальное время для публикации
    
    Args:
        region_code: Код региона
        category: Категория контента
        time_slot: Предпочтительное время суток (опционально)
        
    Returns:
        Оптимальное время с прогнозом engagement
    """
    scheduler = SmartScheduler()
    
    # Получить оптимальное время
    hour, minute = await scheduler.get_optimal_time(region_code, category, time_slot)
    
    # Создать datetime
    now = datetime.now()
    optimal_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    
    if optimal_time <= now:
        optimal_time += timedelta(days=1)
    
    # Получить прогноз engagement
    forecast = await scheduler.get_engagement_forecast(region_code, optimal_time)
    
    return {
        "region_code": region_code,
        "category": category,
        "optimal_hour": hour,
        "optimal_minute": minute,
        "optimal_time": optimal_time.isoformat(),
        "engagement_forecast": forecast["forecast_engagement"],
        "vs_average": forecast["vs_average_pct"],
        "recommendation": forecast["recommendation"]
    }


@router.get("/engagement-report/{region_code}")
@cache(ttl=3600, key_prefix="scheduler")  # Cache for 1 hour
async def get_engagement_report(
    region_code: str,
    days_back: int = Query(90, ge=7, le=365, description="Дней истории для анализа")
):
    """
    Получить полный отчёт по engagement для региона
    
    Показывает:
    - Engagement по часам дня
    - Engagement по дням недели
    - Лучшие и худшие времена
    - Рекомендации
    
    Args:
        region_code: Код региона
        days_back: Период анализа (7-365 дней)
        
    Returns:
        Детальный engagement report
    """
    scheduler = SmartScheduler()
    
    report = await scheduler.get_engagement_report(region_code, days_back)
    
    return report


@router.get("/should-publish-now/{region_code}")
async def should_publish_now(
    region_code: str,
    category: str = Query('novost', description="Категория контента"),
    tolerance_hours: int = Query(2, ge=1, le=6, description="Допустимое отклонение в часах")
):
    """
    Проверить, стоит ли публиковать прямо сейчас
    
    Args:
        region_code: Код региона
        category: Категория
        tolerance_hours: Допустимое отклонение от оптимального времени
        
    Returns:
        Рекомендация о публикации
    """
    scheduler = SmartScheduler()
    
    should_publish, reason = await scheduler.should_publish_now(
        region_code, category, tolerance_hours
    )
    
    # Получить оптимальное время для справки
    hour, minute = await scheduler.get_optimal_time(region_code, category)
    
    return {
        "should_publish": should_publish,
        "reason": reason,
        "current_time": datetime.now().isoformat(),
        "optimal_time": f"{hour}:{minute:02d}",
        "category": category
    }


@router.get("/calendar/{region_code}")
@cache(ttl=900, key_prefix="scheduler")  # Cache for 15 minutes
async def get_publication_calendar(
    region_code: str,
    days: int = Query(7, ge=1, le=30, description="Дней вперёд для календаря")
):
    """
    Получить календарь рекомендуемых публикаций
    
    Args:
        region_code: Код региона
        days: Количество дней вперёд
        
    Returns:
        Календарь с рекомендуемыми слотами
    """
    scheduler = SmartScheduler()
    
    calendar = await scheduler.get_publication_calendar(region_code, days)
    
    return {
        "region_code": region_code,
        "period_days": days,
        "slots_count": len(calendar),
        "slots": calendar
    }


@router.get("/forecast")
async def forecast_engagement(
    region_code: str = Query(..., description="Код региона"),
    publish_time: str = Query(..., description="Время публикации (ISO format)")
):
    """
    Прогноз engagement для конкретного времени
    
    Args:
        region_code: Код региона
        publish_time: Время публикации в ISO format
        
    Returns:
        Прогноз engagement
    """
    try:
        pub_time = datetime.fromisoformat(publish_time)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail="Invalid datetime format. Use ISO format (YYYY-MM-DDTHH:MM:SS)"
        )
    
    scheduler = SmartScheduler()
    forecast = await scheduler.get_engagement_forecast(region_code, pub_time)
    
    return forecast


@router.post("/schedule")
async def schedule_publication(
    request: ScheduleRequest,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Запланировать публикацию дайджеста
    
    Args:
        request: Данные для планирования
        
    Returns:
        Информация о запланированной публикации
    """
    scheduler = SmartScheduler()
    
    # Parse scheduled_time if provided
    scheduled_time = None
    if request.scheduled_time:
        try:
            scheduled_time = datetime.fromisoformat(request.scheduled_time)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail="Invalid datetime format"
            )
    
    # Schedule publication
    result = await scheduler.schedule_publication(
        digest_id=request.digest_id,
        region_code=request.region_code,
        category=request.category,
        scheduled_time=scheduled_time
    )
    
    return result


if __name__ == "__main__":
    print("✅ Smart Scheduler API ready")

