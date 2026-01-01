"""
Rate Limiting Middleware for SETKA
Protects API from DoS attacks and prevents VK API rate limit violations
"""
import time
import redis.asyncio as redis
from fastapi import Request, HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from typing import Dict, Optional
import logging

from config.runtime import REDIS

logger = logging.getLogger(__name__)


class RateLimiter:
    """Redis-based rate limiter"""
    
    def __init__(
        self,
        redis_url: Optional[str] = None,
        requests_per_minute: int = 60,
        burst_size: int = 10
    ):
        """
        Initialize rate limiter
        
        Args:
            redis_url: Redis connection URL
            requests_per_minute: Maximum requests per minute per client
            burst_size: Maximum burst requests allowed
        """
        if redis_url is None:
            redis_url = f"redis://{REDIS['host']}:{REDIS['port']}/{REDIS['db']}"
        
        self.redis_url = redis_url
        self._client = None
        self.requests_per_minute = requests_per_minute
        self.burst_size = burst_size
        self.window_size = 60  # seconds
    
    async def get_client(self) -> redis.Redis:
        """Get or create Redis client"""
        if self._client is None:
            self._client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._client
    
    async def is_allowed(self, client_id: str) -> tuple[bool, dict]:
        """
        Check if request is allowed
        
        Args:
            client_id: Unique identifier for client (usually IP)
            
        Returns:
            (is_allowed, info_dict)
        """
        try:
            client = await self.get_client()
            key = f"rate_limit:{client_id}"
            current_time = int(time.time())
            
            # Use Redis ZSET for sliding window
            # Remove old entries outside window
            await client.zremrangebyscore(
                key,
                0,
                current_time - self.window_size
            )
            
            # Count requests in current window
            request_count = await client.zcard(key)
            
            # Check if limit exceeded
            if request_count >= self.requests_per_minute:
                # Get oldest request time
                oldest = await client.zrange(key, 0, 0, withscores=True)
                if oldest:
                    retry_after = int(oldest[0][1] + self.window_size - current_time)
                else:
                    retry_after = self.window_size
                
                return False, {
                    "requests_remaining": 0,
                    "retry_after": retry_after,
                    "limit": self.requests_per_minute
                }
            
            # Add current request
            await client.zadd(key, {str(current_time): current_time})
            await client.expire(key, self.window_size)
            
            remaining = self.requests_per_minute - request_count - 1
            
            return True, {
                "requests_remaining": remaining,
                "retry_after": 0,
                "limit": self.requests_per_minute
            }
            
        except Exception as e:
            logger.error(f"Rate limiter error: {e}")
            # On error, allow request (fail open)
            return True, {"error": str(e)}
    
    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    FastAPI middleware for rate limiting
    
    Limits requests per IP address to prevent abuse
    """
    
    def __init__(
        self,
        app,
        requests_per_minute: int = 100,
        burst_size: int = 20,
        whitelist: list = None,
        blacklist: list = None
    ):
        """
        Initialize middleware
        
        Args:
            app: FastAPI application
            requests_per_minute: Max requests per minute per IP
            burst_size: Max burst requests
            whitelist: List of IPs to always allow
            blacklist: List of IPs to always block
        """
        super().__init__(app)
        self.rate_limiter = RateLimiter(
            requests_per_minute=requests_per_minute,
            burst_size=burst_size
        )
        self.whitelist = set(whitelist or [])
        self.blacklist = set(blacklist or [])
        
        # Paths to exempt from rate limiting
        self.exempt_paths = {
            "/docs",
            "/redoc",
            "/openapi.json",
            "/favicon.ico",
            "/static",
        }
    
    def get_client_id(self, request: Request) -> str:
        """
        Get unique client identifier
        
        Uses IP address, but can be extended to use API keys, etc.
        """
        # Try to get real IP if behind proxy
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fallback to direct client IP
        if request.client:
            return request.client.host
        
        return "unknown"
    
    def is_exempt(self, path: str) -> bool:
        """Check if path is exempt from rate limiting"""
        for exempt_path in self.exempt_paths:
            if path.startswith(exempt_path):
                return True
        return False
    
    async def dispatch(self, request: Request, call_next):
        """Process request with rate limiting"""
        
        # Skip rate limiting for exempt paths
        if self.is_exempt(request.url.path):
            return await call_next(request)
        
        # Get client identifier
        client_id = self.get_client_id(request)
        
        # Check blacklist
        if client_id in self.blacklist:
            logger.warning(f"Blocked request from blacklisted IP: {client_id}")
            raise HTTPException(
                status_code=403,
                detail="Access forbidden"
            )
        
        # Check whitelist (skip rate limiting)
        if client_id in self.whitelist:
            return await call_next(request)
        
        # Check rate limit
        is_allowed, info = await self.rate_limiter.is_allowed(client_id)
        
        if not is_allowed:
            logger.warning(
                f"Rate limit exceeded for {client_id} on {request.url.path}"
            )
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "Rate limit exceeded",
                    "retry_after": info.get("retry_after", 60),
                    "limit": info.get("limit", 100)
                },
                headers={
                    "Retry-After": str(info.get("retry_after", 60)),
                    "X-RateLimit-Limit": str(info.get("limit", 100)),
                    "X-RateLimit-Remaining": "0"
                }
            )
        
        # Add rate limit headers to response
        response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(info.get("limit", 100))
        response.headers["X-RateLimit-Remaining"] = str(info.get("requests_remaining", 0))
        
        return response


# Global rate limiter instance for VK API
vk_rate_limiter = RateLimiter(requests_per_minute=3)  # VK has strict limits


async def check_vk_rate_limit(token_name: str) -> bool:
    """
    Check if VK API call is allowed for given token
    
    VK API has limit of 3 requests per second
    
    Args:
        token_name: Name of VK token (e.g., 'VALSTAN')
        
    Returns:
        True if allowed, raises HTTPException if not
    """
    is_allowed, info = await vk_rate_limiter.is_allowed(f"vk_token:{token_name}")
    
    if not is_allowed:
        retry_after = info.get("retry_after", 1)
        raise HTTPException(
            status_code=429,
            detail=f"VK API rate limit exceeded. Retry after {retry_after} seconds."
        )
    
    return True


if __name__ == "__main__":
    import asyncio
    
    async def test():
        limiter = RateLimiter(requests_per_minute=5)
        
        print("Testing rate limiter (5 requests per minute):")
        
        for i in range(10):
            is_allowed, info = await limiter.is_allowed("test_client")
            status = "✅ ALLOWED" if is_allowed else "❌ BLOCKED"
            print(f"Request {i+1}: {status} - Remaining: {info.get('requests_remaining', 'N/A')}")
            await asyncio.sleep(0.5)
        
        await limiter.close()
    
    asyncio.run(test())

