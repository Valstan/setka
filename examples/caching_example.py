"""
Example: Using caching in SETKA API endpoints
Demonstrates how to apply caching to improve performance
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from database.connection import get_db_session
from database.models import Region, Post, Community
from utils.cache import cache, invalidate_cache, get_cache
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


# ============================================================================
# EXAMPLE 1: Simple caching with default settings
# ============================================================================

@router.get("/regions")
@cache(ttl=600)  # Cache for 10 minutes
async def get_all_regions(db: AsyncSession = Depends(get_db_session)):
    """
    Get all regions (cached for 10 minutes)
    
    Why cache:
    - Regions rarely change
    - Frequently requested
    - Expensive DB query avoided
    
    Result:
    - First request: ~100ms (DB query)
    - Subsequent requests: ~5ms (from cache)
    - 20x faster!
    """
    logger.info("Fetching regions from database")
    result = await db.execute(select(Region))
    regions = result.scalars().all()
    
    return [
        {
            "id": r.id,
            "code": r.code,
            "name": r.name,
            "is_active": r.is_active
        }
        for r in regions
    ]


# ============================================================================
# EXAMPLE 2: Caching with custom key prefix
# ============================================================================

@router.get("/posts/region/{region_code}")
@cache(ttl=300, key_prefix="posts")  # Cache for 5 minutes
async def get_posts_by_region(
    region_code: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get posts by region (cached)
    
    Cache key will be: "posts:get_posts_by_region:HASH"
    where HASH is based on (region_code, limit)
    
    Different combinations are cached separately:
    - /posts/region/mi?limit=50 -> separate cache
    - /posts/region/mi?limit=100 -> separate cache
    - /posts/region/nolinsk?limit=50 -> separate cache
    """
    logger.info(f"Fetching posts for region {region_code}, limit {limit}")
    
    result = await db.execute(
        select(Post)
        .join(Region)
        .where(Region.code == region_code)
        .order_by(Post.date_published.desc())
        .limit(limit)
    )
    posts = result.scalars().all()
    
    return [
        {
            "id": p.id,
            "text": p.text[:200],
            "views": p.views,
            "likes": p.likes,
            "ai_score": p.ai_score,
            "date_published": p.date_published.isoformat()
        }
        for p in posts
    ]


# ============================================================================
# EXAMPLE 3: Cache with custom key builder
# ============================================================================

def build_community_cache_key(*args, **kwargs) -> str:
    """Custom key builder for community endpoints"""
    region_code = kwargs.get('region_code')
    category = kwargs.get('category', 'all')
    return f"communities:{region_code}:{category}"


@router.get("/communities/{region_code}")
@cache(ttl=900, key_builder=build_community_cache_key)  # 15 minutes
async def get_communities_by_region(
    region_code: str,
    category: str = None,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Get communities by region and category (cached)
    
    Custom key example:
    - communities:mi:novost
    - communities:mi:admin
    - communities:nolinsk:all
    """
    logger.info(f"Fetching communities for {region_code}, category: {category}")
    
    query = select(Community).join(Region).where(Region.code == region_code)
    
    if category:
        query = query.where(Community.category == category)
    
    result = await db.execute(query)
    communities = result.scalars().all()
    
    return [
        {
            "id": c.id,
            "name": c.name,
            "vk_id": c.vk_id,
            "category": c.category,
            "posts_count": c.posts_count
        }
        for c in communities
    ]


# ============================================================================
# EXAMPLE 4: Cache invalidation on updates
# ============================================================================

@router.post("/posts/{post_id}/approve")
async def approve_post(
    post_id: int,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Approve post and invalidate related cache
    
    When post is updated, we need to invalidate:
    - Posts list cache
    - Region stats cache
    - Any aggregated data
    """
    # Update post
    result = await db.execute(select(Post).where(Post.id == post_id))
    post = result.scalar_one()
    
    post.status = 'approved'
    await db.commit()
    
    # Invalidate related caches
    region = await db.get(Region, post.region_id)
    
    # Clear posts cache for this region
    await invalidate_cache(f"posts:*:{region.code}:*")
    
    # Clear stats cache
    await invalidate_cache(f"stats:region:{region.code}:*")
    
    logger.info(f"Post {post_id} approved, cache invalidated")
    
    return {"success": True, "post_id": post_id}


# ============================================================================
# EXAMPLE 5: Manual cache operations
# ============================================================================

@router.get("/stats/cache")
async def get_cache_stats():
    """
    Get cache statistics
    
    Shows:
    - Hit/miss rate
    - Number of keys
    - Memory usage
    """
    cache_client = get_cache()
    stats = await cache_client.get_stats()
    
    return {
        "status": "healthy",
        "cache_stats": stats,
        "recommendations": _get_cache_recommendations(stats)
    }


def _get_cache_recommendations(stats: dict) -> List[str]:
    """Provide recommendations based on cache stats"""
    recommendations = []
    
    hit_rate = stats.get('hit_rate', 0)
    
    if hit_rate < 50:
        recommendations.append("⚠️ Low hit rate (<50%). Consider increasing TTL or caching more endpoints.")
    elif hit_rate > 90:
        recommendations.append("✅ Excellent hit rate (>90%)!")
    
    keys_count = stats.get('keys_count', 0)
    if keys_count > 10000:
        recommendations.append("⚠️ Many keys in cache. Consider shorter TTL or pattern-based cleanup.")
    
    return recommendations


@router.post("/cache/clear")
async def clear_cache(pattern: str = "*"):
    """
    Clear cache by pattern
    
    Examples:
    - pattern="*" - clear all
    - pattern="posts:*" - clear all posts cache
    - pattern="posts:*:mi:*" - clear posts for 'mi' region
    """
    deleted = await invalidate_cache(pattern)
    
    return {
        "success": True,
        "pattern": pattern,
        "keys_deleted": deleted
    }


# ============================================================================
# EXAMPLE 6: Conditional caching based on parameters
# ============================================================================

@router.get("/posts/search")
async def search_posts(
    q: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    """
    Search posts with conditional caching
    
    Only cache if:
    - Query is common (e.g., length > 3 chars)
    - Limit is reasonable
    """
    # Determine if we should cache this query
    should_cache = len(q) >= 3 and limit <= 100
    
    if should_cache:
        # Try to get from cache
        cache_key = f"search:posts:{q}:{limit}"
        cache_client = get_cache()
        cached = await cache_client.get(cache_key)
        
        if cached:
            logger.info(f"Search cache HIT for query: {q}")
            return cached
    
    # Execute search (using full-text search)
    logger.info(f"Executing search for: {q}")
    # ... perform search ...
    
    results = []  # Search results
    
    if should_cache:
        # Cache results for 2 minutes
        await cache_client.set(cache_key, results, ttl=120)
    
    return results


# ============================================================================
# EXAMPLE 7: Warming up cache
# ============================================================================

@router.post("/cache/warmup")
async def warmup_cache(db: AsyncSession = Depends(get_db_session)):
    """
    Pre-populate cache with frequently accessed data
    
    Run this:
    - On application startup
    - After cache clear
    - During off-peak hours
    """
    logger.info("Starting cache warmup...")
    
    cache_client = get_cache()
    warmed = 0
    
    # 1. Warm up regions
    regions_result = await db.execute(select(Region))
    regions = regions_result.scalars().all()
    await cache_client.set("get_all_regions", regions, ttl=600)
    warmed += 1
    
    # 2. Warm up posts for each region
    for region in regions:
        posts_result = await db.execute(
            select(Post)
            .where(Post.region_id == region.id)
            .order_by(Post.date_published.desc())
            .limit(50)
        )
        posts = posts_result.scalars().all()
        
        cache_key = f"posts:get_posts_by_region:{region.code}:50"
        await cache_client.set(cache_key, posts, ttl=300)
        warmed += 1
    
    logger.info(f"Cache warmup complete: {warmed} keys populated")
    
    return {
        "success": True,
        "keys_warmed": warmed
    }


# ============================================================================
# PERFORMANCE COMPARISON
# ============================================================================

@router.get("/demo/performance-comparison")
async def demo_performance_comparison(db: AsyncSession = Depends(get_db_session)):
    """
    Demonstrate performance improvement with caching
    
    Compares:
    - Without cache: Direct DB query
    - With cache: Cached result
    """
    import time
    
    # Test without cache
    start = time.time()
    result = await db.execute(
        select(Post)
        .order_by(Post.date_published.desc())
        .limit(100)
    )
    posts = result.scalars().all()
    time_no_cache = (time.time() - start) * 1000  # ms
    
    # Test with cache (first call - miss)
    cache_client = get_cache()
    cache_key = "demo:posts:100"
    
    start = time.time()
    cached = await cache_client.get(cache_key)
    if not cached:
        result = await db.execute(
            select(Post)
            .order_by(Post.date_published.desc())
            .limit(100)
        )
        cached = result.scalars().all()
        await cache_client.set(cache_key, cached, ttl=60)
    time_first_cached = (time.time() - start) * 1000
    
    # Test with cache (second call - hit)
    start = time.time()
    cached = await cache_client.get(cache_key)
    time_cached_hit = (time.time() - start) * 1000
    
    return {
        "results": {
            "no_cache_ms": round(time_no_cache, 2),
            "first_cached_ms": round(time_first_cached, 2),
            "cached_hit_ms": round(time_cached_hit, 2),
            "improvement": f"{round(time_no_cache / time_cached_hit, 1)}x faster"
        },
        "post_count": len(posts)
    }


# ============================================================================
# USAGE TIPS
# ============================================================================

"""
CACHING BEST PRACTICES:

1. Cache what's expensive:
   - Complex DB queries
   - Aggregations
   - External API calls
   - Heavy computations

2. Choose appropriate TTL:
   - Static data (regions): 10-30 minutes
   - Semi-static (posts): 2-5 minutes
   - Dynamic (stats): 30-60 seconds
   - User-specific: Don't cache or very short TTL

3. Invalidate when needed:
   - On create/update/delete operations
   - Use patterns for bulk invalidation
   - Be careful with cascading invalidations

4. Monitor cache performance:
   - Track hit/miss rates
   - Watch memory usage
   - Identify cache hotspots

5. Handle cache failures gracefully:
   - Always have fallback to DB
   - Log cache errors
   - Don't let cache failures break app

6. Test with and without cache:
   - Ensure correctness
   - Verify invalidation logic
   - Measure performance improvements
"""


