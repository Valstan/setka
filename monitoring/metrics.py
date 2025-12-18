"""
Prometheus Metrics for SETKA
Comprehensive monitoring and observability
"""
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    Info,
    generate_latest,
    CONTENT_TYPE_LATEST
)
from functools import wraps
import time
import logging

logger = logging.getLogger(__name__)

# =============================================================================
# API METRICS
# =============================================================================

# Request counters
api_requests_total = Counter(
    'setka_api_requests_total',
    'Total API requests',
    ['method', 'endpoint', 'status']
)

api_requests_in_progress = Gauge(
    'setka_api_requests_in_progress',
    'API requests currently in progress'
)

# Latency histogram
api_request_duration_seconds = Histogram(
    'setka_api_request_duration_seconds',
    'API request duration in seconds',
    ['method', 'endpoint'],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
)

# =============================================================================
# CACHE METRICS
# =============================================================================

cache_hits_total = Counter(
    'setka_cache_hits_total',
    'Total cache hits',
    ['cache_type']
)

cache_misses_total = Counter(
    'setka_cache_misses_total',
    'Total cache misses',
    ['cache_type']
)

cache_size_bytes = Gauge(
    'setka_cache_size_bytes',
    'Current cache size in bytes'
)

# =============================================================================
# VK API METRICS
# =============================================================================

vk_api_requests_total = Counter(
    'setka_vk_api_requests_total',
    'Total VK API requests',
    ['method', 'status']
)

vk_api_request_duration_seconds = Histogram(
    'setka_vk_api_request_duration_seconds',
    'VK API request duration',
    ['method'],
    buckets=(0.1, 0.25, 0.5, 1.0, 2.0, 5.0)
)

vk_api_errors_total = Counter(
    'setka_vk_api_errors_total',
    'Total VK API errors',
    ['error_code']
)

vk_api_rate_limit_hits = Counter(
    'setka_vk_api_rate_limit_hits_total',
    'VK API rate limit hits'
)

# =============================================================================
# DATABASE METRICS
# =============================================================================

db_queries_total = Counter(
    'setka_db_queries_total',
    'Total database queries',
    ['operation']
)

db_query_duration_seconds = Histogram(
    'setka_db_query_duration_seconds',
    'Database query duration',
    ['operation'],
    buckets=(0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.5, 1.0)
)

db_connections_active = Gauge(
    'setka_db_connections_active',
    'Active database connections'
)

# =============================================================================
# BUSINESS METRICS
# =============================================================================

posts_processed_total = Counter(
    'setka_posts_processed_total',
    'Total posts processed',
    ['status']
)

posts_published_total = Counter(
    'setka_posts_published_total',
    'Total posts published',
    ['channel']
)

communities_monitored = Gauge(
    'setka_communities_monitored',
    'Number of communities being monitored'
)

regions_active = Gauge(
    'setka_regions_active',
    'Number of active regions'
)

# =============================================================================
# SYSTEM METRICS
# =============================================================================

system_info = Info(
    'setka_system',
    'SETKA system information'
)

errors_total = Counter(
    'setka_errors_total',
    'Total errors',
    ['component', 'error_type']
)

# =============================================================================
# DECORATORS
# =============================================================================

def track_api_request(endpoint: str):
    """
    Decorator to track API request metrics
    
    Usage:
        @track_api_request('get_regions')
        async def get_regions():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            api_requests_in_progress.inc()
            start_time = time.time()
            status = "success"
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                raise
            finally:
                duration = time.time() - start_time
                
                # Get method from request if available
                method = "GET"  # Default
                if args:
                    request = next((arg for arg in args if hasattr(arg, 'method')), None)
                    if request:
                        method = request.method
                
                api_requests_total.labels(
                    method=method,
                    endpoint=endpoint,
                    status=status
                ).inc()
                
                api_request_duration_seconds.labels(
                    method=method,
                    endpoint=endpoint
                ).observe(duration)
                
                api_requests_in_progress.dec()
        
        return wrapper
    return decorator


def track_vk_request(method: str):
    """
    Decorator to track VK API request metrics
    
    Usage:
        @track_vk_request('wall.get')
        async def get_wall_posts():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            status = "success"
            
            try:
                result = await func(*args, **kwargs)
                return result
            except Exception as e:
                status = "error"
                
                # Track specific error codes
                if hasattr(e, 'error_code'):
                    vk_api_errors_total.labels(error_code=str(e.error_code)).inc()
                    
                    # Track rate limit hits
                    if e.error_code == 6:
                        vk_api_rate_limit_hits.inc()
                
                raise
            finally:
                duration = time.time() - start_time
                
                vk_api_requests_total.labels(
                    method=method,
                    status=status
                ).inc()
                
                vk_api_request_duration_seconds.labels(
                    method=method
                ).observe(duration)
        
        return wrapper
    return decorator


def track_db_query(operation: str):
    """
    Decorator to track database query metrics
    
    Usage:
        @track_db_query('select_posts')
        async def get_posts():
            ...
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            start_time = time.time()
            
            try:
                result = await func(*args, **kwargs)
                return result
            finally:
                duration = time.time() - start_time
                
                db_queries_total.labels(operation=operation).inc()
                db_query_duration_seconds.labels(operation=operation).observe(duration)
        
        return wrapper
    return decorator


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def track_cache_hit(cache_type: str = 'redis'):
    """Record a cache hit"""
    cache_hits_total.labels(cache_type=cache_type).inc()


def track_cache_miss(cache_type: str = 'redis'):
    """Record a cache miss"""
    cache_misses_total.labels(cache_type=cache_type).inc()


def track_post_processed(status: str):
    """
    Record a processed post
    
    Args:
        status: 'approved', 'rejected', 'analyzed', etc.
    """
    posts_processed_total.labels(status=status).inc()


def track_post_published(channel: str):
    """
    Record a published post
    
    Args:
        channel: 'vk', 'telegram', 'ok', etc.
    """
    posts_published_total.labels(channel=channel).inc()


def track_error(component: str, error_type: str):
    """
    Record an error
    
    Args:
        component: Component where error occurred
        error_type: Type of error
    """
    errors_total.labels(
        component=component,
        error_type=error_type
    ).inc()
    
    logger.error(f"Error tracked: {component}.{error_type}")


def update_system_info(version: str, python_version: str, environment: str = 'production'):
    """Update system information"""
    system_info.info({
        'version': version,
        'python_version': python_version,
        'environment': environment
    })


def get_cache_metrics():
    """
    Get current cache metrics
    
    Returns:
        Dict with cache statistics
    """
    try:
        from utils.cache import get_cache
        import asyncio
        
        cache = get_cache()
        stats = asyncio.run(cache.get_stats())
        
        # Update gauge
        if 'memory_used' in stats:
            # Parse memory (e.g., "1.5M" -> bytes)
            memory_str = stats['memory_used']
            if memory_str != 'N/A':
                # Simple parsing (improve if needed)
                cache_size_bytes.set(0)  # Placeholder
        
        return stats
    except Exception as e:
        logger.error(f"Failed to get cache metrics: {e}")
        return {}


async def update_business_metrics():
    """Update business metrics from database"""
    try:
        from database.connection import AsyncSessionLocal
        from database.models import Community, Region
        from sqlalchemy import select, func
        
        async with AsyncSessionLocal() as session:
            # Count active communities
            result = await session.execute(
                select(func.count(Community.id)).where(Community.is_active == True)
            )
            count = result.scalar()
            communities_monitored.set(count)
            
            # Count active regions
            result = await session.execute(
                select(func.count(Region.id)).where(Region.is_active == True)
            )
            count = result.scalar()
            regions_active.set(count)
    
    except Exception as e:
        logger.error(f"Failed to update business metrics: {e}")


# =============================================================================
# METRICS ENDPOINT
# =============================================================================

def get_metrics():
    """
    Get Prometheus metrics in text format
    
    Returns:
        Tuple of (content, content_type)
    """
    # Update cache metrics before export
    get_cache_metrics()
    
    return generate_latest(), CONTENT_TYPE_LATEST


if __name__ == "__main__":
    # Test metrics
    print("Testing metrics...")
    
    # Simulate some metrics
    api_requests_total.labels(method='GET', endpoint='/test', status='success').inc()
    api_request_duration_seconds.labels(method='GET', endpoint='/test').observe(0.123)
    
    cache_hits_total.labels(cache_type='redis').inc(10)
    cache_misses_total.labels(cache_type='redis').inc(2)
    
    vk_api_requests_total.labels(method='wall.get', status='success').inc(5)
    
    # Generate metrics
    metrics_output = generate_latest().decode('utf-8')
    
    print("\nGenerated metrics:")
    print("=" * 60)
    print(metrics_output[:500])  # First 500 chars
    print("...")
    print("=" * 60)
    print("\n✅ Metrics test completed!")

