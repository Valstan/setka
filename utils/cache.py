"""
Cache utilities for SETKA
Provides Redis-based caching for expensive operations
"""
import redis.asyncio as redis
import pickle
import logging
from functools import wraps
from typing import Any, Optional, Callable
import hashlib
import json

from config.runtime import REDIS

logger = logging.getLogger(__name__)


class RedisCache:
    """Redis cache manager"""
    
    def __init__(self, redis_url: Optional[str] = None):
        """
        Initialize Redis cache
        
        Args:
            redis_url: Redis connection URL (default from config)
        """
        if redis_url is None:
            redis_url = f"redis://{REDIS['host']}:{REDIS['port']}/{REDIS['db']}"
        
        self.redis_url = redis_url
        self._client = None
    
    async def get_client(self) -> redis.Redis:
        """Get or create Redis client"""
        if self._client is None:
            self._client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=False  # We'll use pickle
            )
        return self._client
    
    async def get(self, key: str) -> Optional[Any]:
        """
        Get value from cache
        
        Args:
            key: Cache key
            
        Returns:
            Cached value or None if not found
        """
        try:
            client = await self.get_client()
            value = await client.get(key)
            
            if value is None:
                logger.debug(f"Cache MISS: {key}")
                # Track cache miss
                try:
                    from monitoring.metrics import track_cache_miss
                    track_cache_miss('redis')
                except ImportError:
                    pass
                return None
            
            logger.debug(f"Cache HIT: {key}")
            # Track cache hit
            try:
                from monitoring.metrics import track_cache_hit
                track_cache_hit('redis')
            except ImportError:
                pass
            return pickle.loads(value)
            
        except Exception as e:
            logger.error(f"Cache GET error for key {key}: {e}")
            return None
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """
        Set value in cache
        
        Args:
            key: Cache key
            value: Value to cache (must be picklable)
            ttl: Time to live in seconds (default 5 minutes)
            
        Returns:
            True if successful
        """
        try:
            client = await self.get_client()
            serialized = pickle.dumps(value)
            await client.setex(key, ttl, serialized)
            logger.debug(f"Cache SET: {key} (TTL: {ttl}s)")
            return True
            
        except Exception as e:
            logger.error(f"Cache SET error for key {key}: {e}")
            return False
    
    async def delete(self, key: str) -> bool:
        """
        Delete key from cache
        
        Args:
            key: Cache key
            
        Returns:
            True if deleted
        """
        try:
            client = await self.get_client()
            result = await client.delete(key)
            logger.debug(f"Cache DELETE: {key}")
            return bool(result)
            
        except Exception as e:
            logger.error(f"Cache DELETE error for key {key}: {e}")
            return False
    
    async def clear_pattern(self, pattern: str) -> int:
        """
        Clear all keys matching pattern
        
        Args:
            pattern: Key pattern (e.g., "posts:*")
            
        Returns:
            Number of keys deleted
        """
        try:
            client = await self.get_client()
            keys = []
            
            async for key in client.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                deleted = await client.delete(*keys)
                logger.info(f"Cache CLEAR: {pattern} ({deleted} keys)")
                return deleted
            
            return 0
            
        except Exception as e:
            logger.error(f"Cache CLEAR error for pattern {pattern}: {e}")
            return 0
    
    async def get_stats(self) -> dict:
        """
        Get cache statistics
        
        Returns:
            Dict with cache stats
        """
        try:
            client = await self.get_client()
            info = await client.info('stats')
            
            return {
                'hits': info.get('keyspace_hits', 0),
                'misses': info.get('keyspace_misses', 0),
                'hit_rate': info.get('keyspace_hits', 0) / max(
                    info.get('keyspace_hits', 0) + info.get('keyspace_misses', 0), 1
                ) * 100,
                'keys_count': await client.dbsize(),
                'memory_used': info.get('used_memory_human', 'N/A')
            }
            
        except Exception as e:
            logger.error(f"Cache STATS error: {e}")
            return {}
    
    async def close(self):
        """Close Redis connection"""
        if self._client:
            await self._client.close()
            self._client = None


# Global cache instance
_cache_instance = None


def get_cache() -> RedisCache:
    """Get global cache instance"""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = RedisCache()
    return _cache_instance


def cache(
    ttl: int = 300,
    key_prefix: str = "",
    key_builder: Optional[Callable] = None
):
    """
    Decorator for caching function results
    
    Args:
        ttl: Time to live in seconds (default 5 minutes)
        key_prefix: Prefix for cache key
        key_builder: Custom function to build cache key
        
    Usage:
        @cache(ttl=600)
        async def get_regions():
            # Expensive DB query
            return await db.execute(...)
        
        @cache(ttl=300, key_prefix="posts")
        async def get_posts(region_id: int, limit: int = 10):
            return await db.execute(...)
    """
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Build cache key
            if key_builder:
                cache_key = key_builder(*args, **kwargs)
            else:
                # Default key building
                func_name = func.__name__
                
                # Create hash of arguments for stable key
                args_str = str(args) + str(sorted(kwargs.items()))
                args_hash = hashlib.md5(args_str.encode()).hexdigest()[:8]
                
                cache_key = f"{key_prefix}:{func_name}:{args_hash}" if key_prefix else f"{func_name}:{args_hash}"
            
            # Try to get from cache
            cache_client = get_cache()
            cached_value = await cache_client.get(cache_key)
            
            if cached_value is not None:
                logger.debug(f"Function {func.__name__} returned from cache")
                return cached_value
            
            # Execute function
            logger.debug(f"Function {func.__name__} executing (cache miss)")
            result = await func(*args, **kwargs)
            
            # Store in cache
            await cache_client.set(cache_key, result, ttl)
            
            return result
        
        return wrapper
    return decorator


def cache_key_builder(*args, **kwargs) -> str:
    """
    Build cache key from arguments
    Helper function for custom key building
    """
    parts = []
    
    # Add positional arguments
    for arg in args:
        if hasattr(arg, 'id'):  # For DB models
            parts.append(f"{arg.__class__.__name__}:{arg.id}")
        else:
            parts.append(str(arg))
    
    # Add keyword arguments
    for key, value in sorted(kwargs.items()):
        if hasattr(value, 'id'):
            parts.append(f"{key}:{value.__class__.__name__}:{value.id}")
        else:
            parts.append(f"{key}:{value}")
    
    return ":".join(parts)


async def invalidate_cache(pattern: str):
    """
    Invalidate cache by pattern
    
    Usage:
        # Invalidate all posts cache
        await invalidate_cache("posts:*")
        
        # Invalidate specific region
        await invalidate_cache("posts:get_by_region:mi:*")
    """
    cache_client = get_cache()
    deleted = await cache_client.clear_pattern(pattern)
    logger.info(f"Invalidated {deleted} cache entries matching pattern: {pattern}")
    return deleted


# Example usage in API endpoints
if __name__ == "__main__":
    import asyncio
    
    async def example():
        # Initialize cache
        cache_client = get_cache()
        
        # Set value
        await cache_client.set("test_key", {"data": "test"}, ttl=60)
        
        # Get value
        value = await cache_client.get("test_key")
        print(f"Cached value: {value}")
        
        # Get stats
        stats = await cache_client.get_stats()
        print(f"Cache stats: {stats}")
        
        # Close
        await cache_client.close()
    
    asyncio.run(example())

