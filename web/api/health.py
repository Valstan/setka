"""
Health check endpoints
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from database.connection import get_db_session
from modules.monitoring.health_checker import HealthChecker

router = APIRouter()


@router.get("/")
async def health_check():
    """Quick health check"""
    return {"status": "healthy", "service": "SETKA"}


@router.get("/full")
async def full_health_check():
    """Full system health check"""
    checker = HealthChecker()
    result = await checker.full_health_check()
    return result

