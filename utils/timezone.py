"""
Timezone utilities for SETKA project
Provides Moscow timezone-aware datetime functions
"""
import pytz
from datetime import datetime, timezone
from typing import Optional

# Moscow timezone
MOSCOW_TZ = pytz.timezone('Europe/Moscow')


def now_moscow() -> datetime:
    """
    Get current time in Moscow timezone
    
    Returns:
        datetime object in Moscow timezone
    """
    return datetime.now(MOSCOW_TZ)


def utc_to_moscow(utc_dt: datetime) -> datetime:
    """
    Convert UTC datetime to Moscow timezone
    
    Args:
        utc_dt: UTC datetime object
        
    Returns:
        datetime object in Moscow timezone
    """
    if utc_dt.tzinfo is None:
        utc_dt = utc_dt.replace(tzinfo=timezone.utc)
    return utc_dt.astimezone(MOSCOW_TZ)


def moscow_to_utc(moscow_dt: datetime) -> datetime:
    """
    Convert Moscow datetime to UTC
    
    Args:
        moscow_dt: Moscow datetime object
        
    Returns:
        datetime object in UTC
    """
    if moscow_dt.tzinfo is None:
        moscow_dt = MOSCOW_TZ.localize(moscow_dt)
    return moscow_dt.astimezone(timezone.utc)


def get_moscow_hour() -> int:
    """
    Get current hour in Moscow timezone
    
    Returns:
        Current hour (0-23) in Moscow timezone
    """
    return now_moscow().hour


def is_work_hours_moscow(start_hour: int = 7, end_hour: int = 22, region_name: str = None) -> bool:
    """
    Check if current time is within work hours in Moscow timezone
    
    Args:
        start_hour: Work start hour (default 7)
        end_hour: Work end hour (default 22)
        region_name: Region name for special cases (e.g., "Тест-Инфо" works 24/7)
        
    Returns:
        True if within work hours
    """
    # Специальное исключение для региона "Тест-Инфо" - работает круглосуточно
    if region_name and region_name.lower() in ["тест-инфо", "test-info", "тест инфо"]:
        return True
    
    current_hour = get_moscow_hour()
    return start_hour <= current_hour <= end_hour


def is_work_hours_for_region(region_name: str, start_hour: int = 7, end_hour: int = 22) -> bool:
    """
    Check if current time is within work hours for specific region
    
    Args:
        region_name: Region name
        start_hour: Work start hour (default 7)
        end_hour: Work end hour (default 22)
        
    Returns:
        True if within work hours for this region
    """
    return is_work_hours_moscow(start_hour, end_hour, region_name)


def format_moscow_time(dt: Optional[datetime] = None) -> str:
    """
    Format datetime in Moscow timezone
    
    Args:
        dt: datetime object (default: current time)
        
    Returns:
        Formatted string in Moscow timezone
    """
    if dt is None:
        dt = now_moscow()
    elif dt.tzinfo is None:
        dt = MOSCOW_TZ.localize(dt)
    else:
        dt = dt.astimezone(MOSCOW_TZ)
    
    return dt.strftime("%d.%m.%Y, %H:%M:%S")


# Backward compatibility - replace datetime.utcnow() calls
def utcnow() -> datetime:
    """
    DEPRECATED: Use now_moscow() instead
    Returns current time in Moscow timezone for backward compatibility
    """
    return now_moscow()


if __name__ == "__main__":
    # Test the timezone utilities
    print(f"Current Moscow time: {now_moscow()}")
    print(f"Current Moscow hour: {get_moscow_hour()}")
    print(f"Is work hours: {is_work_hours_moscow()}")
    print(f"Formatted time: {format_moscow_time()}")
    
    # Test conversion
    utc_time = datetime.now(timezone.utc)
    moscow_time = utc_to_moscow(utc_time)
    print(f"UTC: {utc_time}")
    print(f"Moscow: {moscow_time}")
