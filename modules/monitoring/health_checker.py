"""
Health Checker - monitors system health and sends alerts
"""
import asyncio
import psutil
import logging
from typing import Dict, Any, Optional
from datetime import datetime
from sqlalchemy import select, func

from database.connection import AsyncSessionLocal, engine
from database.models import Region, Community, Post
from modules.monitoring.telegram_notifier import TelegramNotifier

logger = logging.getLogger(__name__)


class HealthChecker:
    """System health monitoring"""
    
    def __init__(self, telegram_notifier: Optional[TelegramNotifier] = None):
        """
        Initialize health checker
        
        Args:
            telegram_notifier: Telegram notifier for alerts
        """
        self.notifier = telegram_notifier
        self.last_check = None
    
    async def check_database(self) -> Dict[str, Any]:
        """Check database connectivity and stats"""
        try:
            async with AsyncSessionLocal() as session:
                # Test query
                result = await session.execute(select(func.count(Region.id)))
                regions_count = result.scalar()
                
                result = await session.execute(select(func.count(Community.id)))
                communities_count = result.scalar()
                
                result = await session.execute(select(func.count(Post.id)))
                posts_count = result.scalar()
                
                return {
                    'status': 'healthy',
                    'regions': regions_count,
                    'communities': communities_count,
                    'posts': posts_count
                }
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    def check_system_resources(self) -> Dict[str, Any]:
        """Check system resources"""
        try:
            # CPU
            cpu_percent = psutil.cpu_percent(interval=1)
            
            # Memory
            memory = psutil.virtual_memory()
            
            # Disk
            disk = psutil.disk_usage('/')
            
            return {
                'status': 'healthy',
                'cpu_percent': cpu_percent,
                'memory': {
                    'total': memory.total,
                    'used': memory.used,
                    'available': memory.available,
                    'percent': memory.percent
                },
                'disk': {
                    'total': disk.total,
                    'used': disk.used,
                    'free': disk.free,
                    'percent': disk.percent
                }
            }
        except Exception as e:
            logger.error(f"System resources check failed: {e}")
            return {
                'status': 'unhealthy',
                'error': str(e)
            }
    
    async def full_health_check(self) -> Dict[str, Any]:
        """Perform full health check"""
        logger.info("Performing full health check...")
        
        # Database check
        db_health = await self.check_database()
        
        # System resources check
        system_health = self.check_system_resources()
        
        # Overall status
        overall_status = 'healthy'
        if db_health.get('status') == 'unhealthy' or system_health.get('status') == 'unhealthy':
            overall_status = 'unhealthy'
        
        # Check for warnings
        warnings = []
        if system_health.get('memory', {}).get('percent', 0) > 90:
            warnings.append("High memory usage (>90%)")
        if system_health.get('disk', {}).get('percent', 0) > 85:
            warnings.append("High disk usage (>85%)")
        
        result = {
            'timestamp': datetime.utcnow().isoformat(),
            'status': overall_status,
            'database': db_health,
            'system': system_health,
            'warnings': warnings
        }
        
        self.last_check = result
        
        # Send alert if unhealthy
        if overall_status == 'unhealthy' and self.notifier:
            await self.notifier.send_error_alert(
                "System health check failed",
                module="HealthChecker",
                details=f"DB: {db_health.get('status')}, System: {system_health.get('status')}"
            )
        
        # Send warning if needed
        if warnings and self.notifier:
            await self.notifier.send_message(
                f"⚠️ <b>System Warnings</b>\n\n" + "\n".join(f"• {w}" for w in warnings)
            )
        
        return result
    
    async def start_monitoring(self, interval_seconds: int = 300):
        """
        Start continuous health monitoring
        
        Args:
            interval_seconds: Check interval (default 5 minutes)
        """
        logger.info(f"Starting health monitoring (interval: {interval_seconds}s)")
        
        while True:
            try:
                await self.full_health_check()
                await asyncio.sleep(interval_seconds)
            except Exception as e:
                logger.error(f"Error in health monitoring: {e}")
                await asyncio.sleep(60)


# Import Optional
from typing import Optional

