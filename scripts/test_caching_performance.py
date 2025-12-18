"""
Test caching performance improvements
Measures API response times before and after caching
"""
import asyncio
import time
import httpx
from statistics import mean, median

API_BASE_URL = "http://localhost:8000/api"

async def measure_endpoint(url: str, runs: int = 5) -> dict:
    """
    Measure response time for an endpoint
    
    Args:
        url: Full URL to test
        runs: Number of test runs
        
    Returns:
        Dict with timing statistics
    """
    times = []
    
    async with httpx.AsyncClient() as client:
        for i in range(runs):
            start = time.time()
            try:
                response = await client.get(url, timeout=30.0)
                elapsed = (time.time() - start) * 1000  # Convert to ms
                
                if response.status_code == 200:
                    times.append(elapsed)
                    print(f"  Run {i+1}/{runs}: {elapsed:.2f}ms")
                else:
                    print(f"  Run {i+1}/{runs}: ERROR {response.status_code}")
            except Exception as e:
                print(f"  Run {i+1}/{runs}: ERROR {e}")
            
            # Small delay between requests
            await asyncio.sleep(0.1)
    
    if not times:
        return {"error": "All requests failed"}
    
    return {
        "min": min(times),
        "max": max(times),
        "mean": mean(times),
        "median": median(times),
        "runs": len(times)
    }


async def test_endpoint(endpoint_name: str, url: str):
    """Test single endpoint"""
    print(f"\n{'='*60}")
    print(f"Testing: {endpoint_name}")
    print(f"URL: {url}")
    print(f"{'='*60}")
    
    # First run (cache MISS)
    print("\n🔴 First run (Cache MISS):")
    first_stats = await measure_endpoint(url, runs=1)
    
    if "error" in first_stats:
        print(f"❌ Error: {first_stats['error']}")
        return None
    
    # Wait a bit
    await asyncio.sleep(0.5)
    
    # Second run (cache HIT)
    print("\n🟢 Subsequent runs (Cache HIT):")
    cached_stats = await measure_endpoint(url, runs=5)
    
    if "error" in cached_stats:
        print(f"❌ Error: {cached_stats['error']}")
        return None
    
    # Calculate improvement
    first_time = first_stats["mean"]
    cached_time = cached_stats["mean"]
    improvement = ((first_time - cached_time) / first_time) * 100
    speedup = first_time / cached_time
    
    print(f"\n📊 Results:")
    print(f"  First request:     {first_time:.2f}ms")
    print(f"  Cached (average):  {cached_time:.2f}ms")
    print(f"  Improvement:       {improvement:.1f}%")
    print(f"  Speedup:           {speedup:.1f}x faster")
    
    return {
        "endpoint": endpoint_name,
        "first_ms": first_time,
        "cached_ms": cached_time,
        "improvement_pct": improvement,
        "speedup": speedup
    }


async def clear_cache():
    """Clear Redis cache"""
    print("\n🗑️  Clearing cache...")
    try:
        import redis
        r = redis.Redis(host='localhost', port=6379, db=0)
        r.flushdb()
        print("✅ Cache cleared!")
    except Exception as e:
        print(f"⚠️  Could not clear cache: {e}")


async def main():
    """Run all tests"""
    print("=" * 60)
    print("🧪 CACHING PERFORMANCE TEST")
    print("=" * 60)
    
    # Clear cache before tests
    await clear_cache()
    await asyncio.sleep(1)
    
    # Test endpoints
    endpoints = [
        ("Regions List", f"{API_BASE_URL}/regions/"),
        ("Specific Region", f"{API_BASE_URL}/regions/mi"),
        ("Communities List", f"{API_BASE_URL}/communities/?limit=100"),
        ("Region Communities", f"{API_BASE_URL}/communities/region/1"),
        ("Posts List", f"{API_BASE_URL}/posts/?limit=50"),
    ]
    
    results = []
    
    for name, url in endpoints:
        result = await test_endpoint(name, url)
        if result:
            results.append(result)
        await asyncio.sleep(1)  # Delay between tests
    
    # Print summary
    print("\n" + "="*60)
    print("📈 SUMMARY")
    print("="*60)
    
    if results:
        print(f"\nTested {len(results)} endpoints:\n")
        
        for r in results:
            print(f"  {r['endpoint']}:")
            print(f"    Without cache: {r['first_ms']:.2f}ms")
            print(f"    With cache:    {r['cached_ms']:.2f}ms")
            print(f"    Improvement:   {r['improvement_pct']:.1f}% ({r['speedup']:.1f}x faster)")
            print()
        
        # Overall statistics
        avg_improvement = mean([r['improvement_pct'] for r in results])
        avg_speedup = mean([r['speedup'] for r in results])
        
        print(f"🏆 OVERALL RESULTS:")
        print(f"  Average improvement: {avg_improvement:.1f}%")
        print(f"  Average speedup:     {avg_speedup:.1f}x faster")
        print()
        
        # Recommendations
        if avg_speedup > 5:
            print("✅ EXCELLENT! Cache is working great!")
        elif avg_speedup > 2:
            print("✅ GOOD! Significant improvement from caching.")
        else:
            print("⚠️  Cache improvement is modest. Consider tuning TTL values.")
    else:
        print("\n❌ No successful tests.")
    
    print("\n" + "="*60)
    print("Test completed!")
    print("="*60)


if __name__ == "__main__":
    print("\n⚠️  Make sure FastAPI is running on http://localhost:8000\n")
    asyncio.run(main())

