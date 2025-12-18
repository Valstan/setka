"""
Metrics Middleware for automatic API metrics collection
"""
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi import Request
import time
import logging

from monitoring.metrics import (
    api_requests_total,
    api_requests_in_progress,
    api_request_duration_seconds
)

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware для автоматического сбора метрик API
    
    Собирает:
    - Количество запросов
    - Латентность запросов
    - Статусы ответов
    - Активные запросы
    """
    
    def __init__(self, app):
        super().__init__(app)
        
        # Paths to exclude from metrics
        self.exclude_paths = {
            '/metrics',
            '/health',
            '/favicon.ico',
        }
    
    def should_track(self, path: str) -> bool:
        """Check if path should be tracked"""
        # Exclude specific paths
        if path in self.exclude_paths:
            return False
        
        # Exclude static files
        if path.startswith('/static'):
            return False
        
        return True
    
    async def dispatch(self, request: Request, call_next):
        """Process request with metrics collection"""
        
        # Check if should track
        if not self.should_track(request.url.path):
            return await call_next(request)
        
        # Increment in-progress counter
        api_requests_in_progress.inc()
        
        # Start timing
        start_time = time.time()
        status_code = 500  # Default to error
        
        try:
            # Process request
            response = await call_next(request)
            status_code = response.status_code
            return response
        
        except Exception as e:
            logger.error(f"Request error: {e}")
            raise
        
        finally:
            # Calculate duration
            duration = time.time() - start_time
            
            # Decrement in-progress counter
            api_requests_in_progress.dec()
            
            # Determine status category
            status = 'success' if 200 <= status_code < 400 else 'error'
            
            # Get endpoint (clean path)
            endpoint = request.url.path
            
            # Remove IDs from path for cleaner metrics
            # /api/posts/123 -> /api/posts/{id}
            parts = endpoint.split('/')
            clean_parts = []
            for part in parts:
                if part.isdigit():
                    clean_parts.append('{id}')
                else:
                    clean_parts.append(part)
            endpoint = '/'.join(clean_parts)
            
            # Record metrics
            api_requests_total.labels(
                method=request.method,
                endpoint=endpoint,
                status=status
            ).inc()
            
            api_request_duration_seconds.labels(
                method=request.method,
                endpoint=endpoint
            ).observe(duration)
            
            # Log slow requests
            if duration > 1.0:
                logger.warning(
                    f"Slow request: {request.method} {endpoint} took {duration:.2f}s"
                )


if __name__ == "__main__":
    print("✅ Metrics middleware module ready")

