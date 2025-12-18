"""
Service Notifications API - Web interface for monitoring system work
"""
from fastapi import APIRouter, HTTPException
from typing import Dict, List, Optional
from pydantic import BaseModel
import logging

from modules.service_notifications import service_notifications, NotificationType

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/service-notifications", tags=["service-notifications"])


class NotificationResponse(BaseModel):
    success: bool
    message: str
    data: Optional[Dict] = None


@router.get("/", response_model=Dict)
async def get_recent_notifications(limit: int = 50):
    """Get recent service notifications"""
    try:
        notifications = service_notifications.get_recent_notifications(limit)
        return {
            "success": True,
            "data": {
                "notifications": notifications,
                "count": len(notifications)
            }
        }
    except Exception as e:
        logger.error(f"Error getting notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status", response_model=Dict)
async def get_system_status():
    """Get current system status"""
    try:
        status = service_notifications.get_status()
        return {
            "success": True,
            "data": status
        }
    except Exception as e:
        logger.error(f"Error getting status: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/by-type/{notification_type}", response_model=Dict)
async def get_notifications_by_type(notification_type: str):
    """Get notifications by type"""
    try:
        # Validate notification type
        try:
            notif_type = NotificationType(notification_type)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid notification type: {notification_type}")
        
        notifications = service_notifications.get_notifications_by_type(notif_type)
        return {
            "success": True,
            "data": {
                "notifications": notifications,
                "count": len(notifications),
                "type": notification_type
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting notifications by type: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/clear", response_model=NotificationResponse)
async def clear_notifications():
    """Clear all notifications"""
    try:
        service_notifications.clear_notifications()
        return NotificationResponse(
            success=True,
            message="All notifications cleared",
            data={"count": 0}
        )
    except Exception as e:
        logger.error(f"Error clearing notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/types", response_model=Dict)
async def get_notification_types():
    """Get available notification types"""
    try:
        types = [nt.value for nt in NotificationType]
        return {
            "success": True,
            "data": {
                "types": types,
                "count": len(types)
            }
        }
    except Exception as e:
        logger.error(f"Error getting notification types: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/live", response_model=Dict)
async def get_live_notifications():
    """Get live notifications (last 10)"""
    try:
        notifications = service_notifications.get_recent_notifications(10)
        status = service_notifications.get_status()
        
        return {
            "success": True,
            "data": {
                "notifications": notifications,
                "status": status,
                "timestamp": service_notifications.notifications[-1].timestamp.isoformat() if service_notifications.notifications else None
            }
        }
    except Exception as e:
        logger.error(f"Error getting live notifications: {e}")
        raise HTTPException(status_code=500, detail=str(e))
