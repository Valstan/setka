"""
Schedule Management API - API для управления расписанием публикаций
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from typing import List, Dict, Any, Optional
from datetime import datetime, time
from pydantic import BaseModel

from database.connection import get_db_session
from database.models import PublishSchedule, Region

router = APIRouter(prefix="/api/schedule", tags=["Schedule Management"])


class ScheduleItem(BaseModel):
    """Элемент расписания"""
    id: Optional[int] = None
    region_id: int
    category: str
    hour: int
    minute: int
    days_of_week: str = "0,1,2,3,4,5,6"
    is_active: bool = True


class ScheduleResponse(BaseModel):
    """Ответ с расписанием"""
    success: bool
    data: List[Dict[str, Any]]
    message: Optional[str] = None


@router.get("/all", response_model=ScheduleResponse)
async def get_all_schedules(session: AsyncSession = Depends(get_db_session)):
    """Получить все расписания"""
    try:
        result = await session.execute(
            select(PublishSchedule, Region)
            .join(Region, PublishSchedule.region_id == Region.id)
            .order_by(PublishSchedule.hour, PublishSchedule.minute)
        )
        
        schedules = []
        for schedule, region in result:
            schedules.append({
                "id": schedule.id,
                "region_id": schedule.region_id,
                "region_name": region.name,
                "region_code": region.code,
                "category": schedule.category,
                "hour": schedule.hour,
                "minute": schedule.minute,
                "time": f"{schedule.hour:02d}:{schedule.minute:02d}",
                "days_of_week": schedule.days_of_week,
                "is_active": schedule.is_active,
                "last_run": schedule.last_run.isoformat() if schedule.last_run else None,
                "created_at": schedule.created_at.isoformat()
            })
        
        return ScheduleResponse(success=True, data=schedules)
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/region/{region_code}", response_model=ScheduleResponse)
async def get_region_schedule(region_code: str, session: AsyncSession = Depends(get_db_session)):
    """Получить расписание для конкретного региона"""
    try:
        # Находим регион
        region_result = await session.execute(select(Region).where(Region.code == region_code))
        region = region_result.scalar_one_or_none()
        
        if not region:
            raise HTTPException(status_code=404, detail="Регион не найден")
        
        # Получаем расписание региона
        result = await session.execute(
            select(PublishSchedule)
            .where(PublishSchedule.region_id == region.id)
            .order_by(PublishSchedule.hour, PublishSchedule.minute)
        )
        
        schedules = []
        for schedule in result.scalars():
            schedules.append({
                "id": schedule.id,
                "region_id": schedule.region_id,
                "region_name": region.name,
                "region_code": region.code,
                "category": schedule.category,
                "hour": schedule.hour,
                "minute": schedule.minute,
                "time": f"{schedule.hour:02d}:{schedule.minute:02d}",
                "days_of_week": schedule.days_of_week,
                "is_active": schedule.is_active,
                "last_run": schedule.last_run.isoformat() if schedule.last_run else None,
                "created_at": schedule.created_at.isoformat()
            })
        
        return ScheduleResponse(success=True, data=schedules)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add", response_model=ScheduleResponse)
async def add_schedule_item(schedule: ScheduleItem, session: AsyncSession = Depends(get_db_session)):
    """Добавить элемент расписания"""
    try:
        # Проверяем, что регион существует
        region_result = await session.execute(select(Region).where(Region.id == schedule.region_id))
        region = region_result.scalar_one_or_none()
        
        if not region:
            raise HTTPException(status_code=404, detail="Регион не найден")
        
        # Проверяем, что время корректное
        if not (0 <= schedule.hour <= 23 and 0 <= schedule.minute <= 59):
            raise HTTPException(status_code=400, detail="Некорректное время")
        
        # Проверяем, что нет дубликатов
        existing = await session.execute(
            select(PublishSchedule).where(
                and_(
                    PublishSchedule.region_id == schedule.region_id,
                    PublishSchedule.hour == schedule.hour,
                    PublishSchedule.minute == schedule.minute,
                    PublishSchedule.category == schedule.category
                )
            )
        )
        
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="Расписание на это время уже существует")
        
        # Создаем новое расписание
        new_schedule = PublishSchedule(
            region_id=schedule.region_id,
            category=schedule.category,
            hour=schedule.hour,
            minute=schedule.minute,
            days_of_week=schedule.days_of_week,
            is_active=schedule.is_active
        )
        
        session.add(new_schedule)
        await session.commit()
        await session.refresh(new_schedule)
        
        return ScheduleResponse(
            success=True, 
            data=[{
                "id": new_schedule.id,
                "region_id": new_schedule.region_id,
                "region_name": region.name,
                "region_code": region.code,
                "category": new_schedule.category,
                "hour": new_schedule.hour,
                "minute": new_schedule.minute,
                "time": f"{new_schedule.hour:02d}:{new_schedule.minute:02d}",
                "days_of_week": new_schedule.days_of_week,
                "is_active": new_schedule.is_active,
                "last_run": None,
                "created_at": new_schedule.created_at.isoformat()
            }],
            message="Элемент расписания добавлен"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/update/{schedule_id}", response_model=ScheduleResponse)
async def update_schedule_item(schedule_id: int, schedule: ScheduleItem, session: AsyncSession = Depends(get_db_session)):
    """Обновить элемент расписания"""
    try:
        # Находим существующее расписание
        result = await session.execute(select(PublishSchedule).where(PublishSchedule.id == schedule_id))
        existing_schedule = result.scalar_one_or_none()
        
        if not existing_schedule:
            raise HTTPException(status_code=404, detail="Элемент расписания не найден")
        
        # Проверяем, что время корректное
        if not (0 <= schedule.hour <= 23 and 0 <= schedule.minute <= 59):
            raise HTTPException(status_code=400, detail="Некорректное время")
        
        # Обновляем поля
        existing_schedule.category = schedule.category
        existing_schedule.hour = schedule.hour
        existing_schedule.minute = schedule.minute
        existing_schedule.days_of_week = schedule.days_of_week
        existing_schedule.is_active = schedule.is_active
        
        await session.commit()
        await session.refresh(existing_schedule)
        
        # Получаем информацию о регионе
        region_result = await session.execute(select(Region).where(Region.id == existing_schedule.region_id))
        region = region_result.scalar_one()
        
        return ScheduleResponse(
            success=True,
            data=[{
                "id": existing_schedule.id,
                "region_id": existing_schedule.region_id,
                "region_name": region.name,
                "region_code": region.code,
                "category": existing_schedule.category,
                "hour": existing_schedule.hour,
                "minute": existing_schedule.minute,
                "time": f"{existing_schedule.hour:02d}:{existing_schedule.minute:02d}",
                "days_of_week": existing_schedule.days_of_week,
                "is_active": existing_schedule.is_active,
                "last_run": existing_schedule.last_run.isoformat() if existing_schedule.last_run else None,
                "created_at": existing_schedule.created_at.isoformat()
            }],
            message="Элемент расписания обновлён"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/delete/{schedule_id}", response_model=ScheduleResponse)
async def delete_schedule_item(schedule_id: int, session: AsyncSession = Depends(get_db_session)):
    """Удалить элемент расписания"""
    try:
        # Находим существующее расписание
        result = await session.execute(select(PublishSchedule).where(PublishSchedule.id == schedule_id))
        existing_schedule = result.scalar_one_or_none()
        
        if not existing_schedule:
            raise HTTPException(status_code=404, detail="Элемент расписания не найден")
        
        await session.delete(existing_schedule)
        await session.commit()
        
        return ScheduleResponse(
            success=True,
            data=[],
            message="Элемент расписания удалён"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/generate-default", response_model=ScheduleResponse)
async def generate_default_schedule(session: AsyncSession = Depends(get_db_session)):
    """Создать расписание по умолчанию для всех регионов"""
    try:
        # Получаем все регионы
        regions_result = await session.execute(select(Region))
        regions = regions_result.scalars().all()
        
        if not regions:
            raise HTTPException(status_code=404, detail="Нет регионов в системе")
        
        # Расписание по умолчанию (7:00 - 23:00)
        default_schedule = [
            (7, 0, "novost", "Утренние новости"),
            (8, 30, "kultura", "Культурные события"),
            (10, 0, "reklama", "Реклама и объявления"),
            (12, 0, "novost", "Дневные новости"),
            (14, 0, "administratsiya", "Новости администрации"),
            (16, 0, "sport", "Спортивные новости"),
            (18, 0, "novost", "Вечерние новости"),
            (20, 0, "kultura", "Культурные мероприятия"),
            (22, 0, "novost", "Ночные новости")
        ]
        
        created_schedules = []
        
        for region in regions:
            for hour, minute, category, description in default_schedule:
                # Проверяем, не существует ли уже такое расписание
                existing = await session.execute(
                    select(PublishSchedule).where(
                        and_(
                            PublishSchedule.region_id == region.id,
                            PublishSchedule.hour == hour,
                            PublishSchedule.minute == minute,
                            PublishSchedule.category == category
                        )
                    )
                )
                
                if not existing.scalar_one_or_none():
                    new_schedule = PublishSchedule(
                        region_id=region.id,
                        category=category,
                        hour=hour,
                        minute=minute,
                        days_of_week="0,1,2,3,4,5,6",
                        is_active=True
                    )
                    session.add(new_schedule)
                    created_schedules.append({
                        "region_name": region.name,
                        "region_code": region.code,
                        "category": category,
                        "time": f"{hour:02d}:{minute:02d}",
                        "description": description
                    })
        
        await session.commit()
        
        return ScheduleResponse(
            success=True,
            data=created_schedules,
            message=f"Создано {len(created_schedules)} элементов расписания"
        )
        
    except HTTPException:
        raise
    except Exception as e:
        await session.rollback()
        raise HTTPException(status_code=500, detail=str(e))
