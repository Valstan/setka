"""
Retry utilities with exponential backoff
Graceful handling of temporary failures
"""
import asyncio
import logging
from typing import Callable, Any, TypeVar, Optional
from functools import wraps
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log,
    after_log
)

from core.exceptions import (
    VKAPIException,
    VKRateLimitException,
    DatabaseException,
    CacheException,
    ExternalServiceException
)

logger = logging.getLogger(__name__)

T = TypeVar('T')


# =============================================================================
# RETRY DECORATORS
# =============================================================================

def retry_vk_api(max_attempts: int = 3):
    """
    Retry decorator for VK API calls
    
    Automatically retries on:
    - Rate limit errors (with backoff)
    - Temporary network errors
    - Timeout errors
    
    Usage:
        @retry_vk_api(max_attempts=3)
        async def get_posts():
            return await vk_client.get_wall_posts(...)
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=2, max=30),
        retry=retry_if_exception_type((
            VKRateLimitException,
            asyncio.TimeoutError,
            ConnectionError
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO)
    )


def retry_database(max_attempts: int = 3):
    """
    Retry decorator for database operations
    
    Automatically retries on:
    - Connection errors
    - Temporary database errors
    
    Usage:
        @retry_database(max_attempts=3)
        async def save_post(post):
            await db.commit()
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception_type((
            DatabaseException,
            ConnectionError,
            asyncio.TimeoutError
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO)
    )


def retry_external_api(max_attempts: int = 3):
    """
    Retry decorator for external API calls (Groq, etc.)
    
    Usage:
        @retry_external_api(max_attempts=3)
        async def analyze_with_groq(text):
            return await groq_client.analyze(text)
    """
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=2, min=4, max=60),
        retry=retry_if_exception_type((
            ExternalServiceException,
            asyncio.TimeoutError,
            ConnectionError
        )),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        after=after_log(logger, logging.INFO)
    )


# =============================================================================
# ADVANCED RETRY FUNCTIONS
# =============================================================================

async def retry_with_fallback(
    primary_func: Callable,
    fallback_func: Callable,
    max_attempts: int = 3,
    *args,
    **kwargs
) -> Any:
    """
    Retry primary function, use fallback if all attempts fail
    
    Usage:
        result = await retry_with_fallback(
            primary_func=groq_api.analyze,
            fallback_func=keyword_analyzer.analyze,
            text="Some post text"
        )
    
    Args:
        primary_func: Primary function to try
        fallback_func: Fallback function if primary fails
        max_attempts: Max attempts for primary
        *args, **kwargs: Arguments for functions
        
    Returns:
        Result from primary or fallback function
    """
    # Try primary function with retries
    for attempt in range(1, max_attempts + 1):
        try:
            logger.info(f"Attempting {primary_func.__name__} (attempt {attempt}/{max_attempts})")
            result = await primary_func(*args, **kwargs)
            logger.info(f"✅ {primary_func.__name__} succeeded")
            return result
        
        except Exception as e:
            logger.warning(f"❌ {primary_func.__name__} failed: {e}")
            
            if attempt < max_attempts:
                # Exponential backoff
                wait_time = min(2 ** attempt, 30)
                logger.info(f"Retrying in {wait_time}s...")
                await asyncio.sleep(wait_time)
            else:
                logger.warning(f"All attempts for {primary_func.__name__} failed")
    
    # All primary attempts failed, use fallback
    try:
        logger.info(f"Using fallback: {fallback_func.__name__}")
        result = await fallback_func(*args, **kwargs)
        logger.info(f"✅ Fallback {fallback_func.__name__} succeeded")
        return result
    
    except Exception as e:
        logger.error(f"❌ Fallback {fallback_func.__name__} also failed: {e}")
        raise SetkaException(
            f"Both primary and fallback failed: {e}",
            details={
                "primary": primary_func.__name__,
                "fallback": fallback_func.__name__,
                "error": str(e)
            }
        )


async def retry_with_circuit_breaker(
    func: Callable,
    circuit_breaker,
    *args,
    **kwargs
) -> Any:
    """
    Retry with circuit breaker pattern
    
    Prevents cascading failures by "opening circuit" after failures
    
    Args:
        func: Function to call
        circuit_breaker: CircuitBreaker instance
        *args, **kwargs: Function arguments
        
    Returns:
        Function result
    """
    if not circuit_breaker.is_closed():
        raise SetkaException(
            f"Circuit breaker is OPEN for {func.__name__}",
            details={"state": circuit_breaker.state}
        )
    
    try:
        result = await func(*args, **kwargs)
        circuit_breaker.record_success()
        return result
    
    except Exception as e:
        circuit_breaker.record_failure()
        raise


# =============================================================================
# CIRCUIT BREAKER
# =============================================================================

class CircuitBreaker:
    """
    Circuit Breaker pattern implementation
    
    States:
    - CLOSED: Normal operation
    - OPEN: Too many failures, reject requests
    - HALF_OPEN: Testing if service recovered
    """
    
    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: int = 60,
        expected_exception: type = Exception
    ):
        """
        Initialize circuit breaker
        
        Args:
            failure_threshold: Number of failures before opening
            recovery_timeout: Seconds before trying again
            expected_exception: Exception type to track
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time = None
        self.state = 'CLOSED'
    
    def is_closed(self) -> bool:
        """Check if circuit is closed (accepting requests)"""
        if self.state == 'CLOSED':
            return True
        
        if self.state == 'OPEN':
            # Check if recovery timeout passed
            if self.last_failure_time:
                import time
                if time.time() - self.last_failure_time >= self.recovery_timeout:
                    logger.info(f"Circuit breaker transitioning to HALF_OPEN")
                    self.state = 'HALF_OPEN'
                    return True
            return False
        
        if self.state == 'HALF_OPEN':
            return True
        
        return False
    
    def record_success(self):
        """Record successful call"""
        if self.state == 'HALF_OPEN':
            logger.info("Circuit breaker CLOSED (service recovered)")
            self.state = 'CLOSED'
        
        self.failure_count = 0
        self.last_failure_time = None
    
    def record_failure(self):
        """Record failed call"""
        import time
        
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            if self.state == 'CLOSED':
                logger.warning(
                    f"Circuit breaker OPENED (failures: {self.failure_count})"
                )
                self.state = 'OPEN'
            elif self.state == 'HALF_OPEN':
                logger.warning("Circuit breaker back to OPEN (test failed)")
                self.state = 'OPEN'


# =============================================================================
# ERROR LOGGING
# =============================================================================

async def log_error_to_db(
    component: str,
    error_type: str,
    error_message: str,
    details: dict = None
):
    """
    Log error to database for analysis
    
    Args:
        component: Component where error occurred
        error_type: Type of error
        error_message: Error message
        details: Additional details (JSON)
    """
    try:
        from database.connection import AsyncSessionLocal
        from datetime import datetime
        
        # TODO: Create error_logs table
        # For now, just log to file
        logger.error(
            f"[{component}] {error_type}: {error_message}",
            extra={"details": details}
        )
        
        # Track in metrics
        from monitoring.metrics import track_error
        track_error(component, error_type)
    
    except Exception as e:
        logger.error(f"Failed to log error to DB: {e}")


# =============================================================================
# EXAMPLE USAGE
# =============================================================================

if __name__ == "__main__":
    print("Testing retry utilities...")
    
    # Test circuit breaker
    cb = CircuitBreaker(failure_threshold=3, recovery_timeout=5)
    
    print(f"\n1. Initial state: {cb.state}")
    print(f"   Is closed: {cb.is_closed()}")
    
    # Simulate failures
    for i in range(5):
        cb.record_failure()
        print(f"\n2. After failure {i+1}:")
        print(f"   State: {cb.state}")
        print(f"   Is closed: {cb.is_closed()}")
    
    # Simulate success
    cb.record_success()
    print(f"\n3. After success:")
    print(f"   State: {cb.state}")
    print(f"   Is closed: {cb.is_closed()}")
    
    print("\n✅ Circuit breaker test completed!")
    
    # Test async retry
    async def test_retry():
        attempt = 0
        
        @retry_vk_api(max_attempts=3)
        async def failing_function():
            nonlocal attempt
            attempt += 1
            print(f"   Attempt {attempt}")
            
            if attempt < 3:
                raise VKRateLimitException()
            
            return "Success!"
        
        try:
            result = await failing_function()
            print(f"\n✅ Retry test: {result}")
        except Exception as e:
            print(f"\n❌ Retry test failed: {e}")
    
    print("\n4. Testing retry decorator:")
    asyncio.run(test_retry())
    
    print("\n✅ All tests completed!")

