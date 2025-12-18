"""
Examples of improved error handling in SETKA
Demonstrates exception hierarchy, retry logic, and circuit breaker
"""
import sys
import os

# Add project root to path
sys.path.insert(0, '/home/valstan/SETKA')

import asyncio
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# =============================================================================
# EXAMPLE 1: Using custom exceptions
# =============================================================================

async def example_custom_exceptions():
    """Пример использования кастомных исключений"""
    from core.exceptions import (
        NotFoundException,
        DuplicateException,
        ValidationException
    )
    
    print("\n" + "="*60)
    print("EXAMPLE 1: Custom Exceptions")
    print("="*60)
    
    # Not found
    try:
        raise NotFoundException("Region", "mi")
    except NotFoundException as e:
        print(f"\n✅ NotFoundException caught:")
        print(f"   Message: {e.message}")
        print(f"   Dict: {e.to_dict()}")
    
    # Duplicate
    try:
        raise DuplicateException("Community", "vk_id", -123456)
    except DuplicateException as e:
        print(f"\n✅ DuplicateException caught:")
        print(f"   Message: {e.message}")
        print(f"   Dict: {e.to_dict()}")


# =============================================================================
# EXAMPLE 2: VK API error handling
# =============================================================================

async def example_vk_error_handling():
    """Пример обработки VK API ошибок"""
    from core.exceptions import (
        VKRateLimitException,
        VKAccessDeniedException,
        VKTokenInvalidException,
        handle_vk_error
    )
    
    print("\n" + "="*60)
    print("EXAMPLE 2: VK API Error Handling")
    print("="*60)
    
    # Simulate different VK errors
    vk_errors = [
        (5, "User authorization failed: invalid access_token"),
        (6, "Too many requests per second"),
        (15, "Access denied: wall is disabled"),
    ]
    
    for error_code, error_msg in vk_errors:
        try:
            handle_vk_error(error_code, error_msg, method="wall.get")
        except VKTokenInvalidException as e:
            print(f"\n✅ Token invalid (code {e.error_code}): {e.message}")
        except VKRateLimitException as e:
            print(f"\n✅ Rate limit (code {e.error_code}): {e.message}")
            print(f"   Retry after: {e.retry_after}s")
        except VKAccessDeniedException as e:
            print(f"\n✅ Access denied (code {e.error_code}): {e.message}")


# =============================================================================
# EXAMPLE 3: Retry with exponential backoff
# =============================================================================

async def example_retry_logic():
    """Пример retry с exponential backoff"""
    from utils.retry import retry_vk_api
    from core.exceptions import VKRateLimitException
    
    print("\n" + "="*60)
    print("EXAMPLE 3: Retry Logic")
    print("="*60)
    
    attempt_count = 0
    
    @retry_vk_api(max_attempts=3)
    async def flaky_vk_call():
        """Симуляция нестабильного VK API вызова"""
        nonlocal attempt_count
        attempt_count += 1
        
        print(f"\n   Attempt {attempt_count}/3...")
        
        if attempt_count < 3:
            # Fail first 2 attempts
            raise VKRateLimitException(retry_after=1)
        
        # Success on 3rd attempt
        return {"status": "success", "data": [1, 2, 3]}
    
    try:
        result = await flaky_vk_call()
        print(f"\n✅ Success after {attempt_count} attempts!")
        print(f"   Result: {result}")
    except Exception as e:
        print(f"\n❌ Failed after all attempts: {e}")


# =============================================================================
# EXAMPLE 4: Retry with fallback
# =============================================================================

async def example_retry_with_fallback():
    """Пример retry с fallback функцией"""
    from utils.retry import retry_with_fallback
    
    print("\n" + "="*60)
    print("EXAMPLE 4: Retry with Fallback")
    print("="*60)
    
    async def primary_function(text: str):
        """Primary function (Groq API)"""
        print("   Trying primary function (Groq API)...")
        # Simulate failure
        raise Exception("Groq API unavailable")
    
    async def fallback_function(text: str):
        """Fallback function (keyword analysis)"""
        print("   Using fallback function (keyword analysis)...")
        return {
            "category": "novost",
            "relevance": 75,
            "method": "keyword"
        }
    
    result = await retry_with_fallback(
        primary_func=primary_function,
        fallback_func=fallback_function,
        max_attempts=2,
        text="Some post text"
    )
    
    print(f"\n✅ Result (from fallback):")
    print(f"   {result}")


# =============================================================================
# EXAMPLE 5: Circuit Breaker
# =============================================================================

async def example_circuit_breaker():
    """Пример Circuit Breaker pattern"""
    from utils.retry import CircuitBreaker
    
    print("\n" + "="*60)
    print("EXAMPLE 5: Circuit Breaker")
    print("="*60)
    
    circuit_breaker = CircuitBreaker(
        failure_threshold=3,
        recovery_timeout=5
    )
    
    print(f"\nInitial state: {circuit_breaker.state}")
    
    # Simulate failures
    print("\nSimulating failures...")
    for i in range(5):
        if circuit_breaker.is_closed():
            print(f"   Attempt {i+1}: Circuit CLOSED, allowing request")
            circuit_breaker.record_failure()
            print(f"   State after failure: {circuit_breaker.state}")
        else:
            print(f"   Attempt {i+1}: Circuit OPEN, rejecting request!")
    
    # Wait for recovery
    print(f"\nWaiting for recovery timeout (5s)...")
    await asyncio.sleep(5)
    
    if circuit_breaker.is_closed():
        print(f"✅ Circuit transitioned to HALF_OPEN, allowing test request")
        circuit_breaker.record_success()
        print(f"✅ Test succeeded, circuit now: {circuit_breaker.state}")


# =============================================================================
# EXAMPLE 6: Error logging to database
# =============================================================================

async def example_error_logging():
    """Пример логирования ошибок"""
    from utils.retry import log_error_to_db
    
    print("\n" + "="*60)
    print("EXAMPLE 6: Error Logging")
    print("="*60)
    
    # Log error to database
    await log_error_to_db(
        component="vk_monitor",
        error_type="VKAPIException",
        error_message="Failed to fetch posts",
        details={
            "community_id": 123,
            "error_code": 15,
            "attempt": 3
        }
    )
    
    print("\n✅ Error logged (check logs/app.log and metrics)")


# =============================================================================
# EXAMPLE 7: Real-world usage in API
# =============================================================================

async def example_api_endpoint():
    """Пример использования в API endpoint"""
    from core.exceptions import NotFoundException, SetkaException
    from utils.retry import retry_database
    
    print("\n" + "="*60)
    print("EXAMPLE 7: Real-world API Endpoint")
    print("="*60)
    
    @retry_database(max_attempts=3)
    async def get_region_from_db(region_code: str):
        """Simulate DB query with retry"""
        print(f"   Querying database for region '{region_code}'...")
        
        # Simulate: region not found
        if region_code == "nonexistent":
            raise NotFoundException("Region", region_code)
        
        # Simulate: successful query
        return {
            "code": region_code,
            "name": "Test Region",
            "is_active": True
        }
    
    # Test successful case
    try:
        region = await get_region_from_db("mi")
        print(f"\n✅ Region found: {region}")
    except SetkaException as e:
        print(f"\n❌ Error: {e.to_dict()}")
    
    # Test not found case
    try:
        region = await get_region_from_db("nonexistent")
    except NotFoundException as e:
        print(f"\n✅ Not found exception handled properly:")
        print(f"   {e.to_dict()}")


# =============================================================================
# RUN ALL EXAMPLES
# =============================================================================

async def main():
    """Run all examples"""
    print("\n" + "="*60)
    print("🧪 ERROR HANDLING EXAMPLES FOR SETKA")
    print("="*60)
    
    await example_custom_exceptions()
    await example_vk_error_handling()
    await example_retry_logic()
    await example_retry_with_fallback()
    await example_circuit_breaker()
    await example_error_logging()
    await example_api_endpoint()
    
    print("\n" + "="*60)
    print("✅ ALL EXAMPLES COMPLETED!")
    print("="*60)
    
    print("\n📚 Key Takeaways:")
    print("  1. Structured exception hierarchy")
    print("  2. Automatic retries with exponential backoff")
    print("  3. Circuit breaker prevents cascading failures")
    print("  4. Error tracking in metrics")
    print("  5. Graceful fallbacks")
    print("\n🚀 Your application is now much more reliable!")


if __name__ == "__main__":
    asyncio.run(main())

