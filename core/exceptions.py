"""
SETKA Exception Hierarchy
Structured exception handling for better error management
"""


class SetkaException(Exception):
    """
    Базовое исключение SETKA
    
    Все исключения проекта наследуются от этого класса
    """
    def __init__(self, message: str, details: dict = None):
        self.message = message
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> dict:
        """Convert exception to dict for API responses"""
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "details": self.details
        }


# =============================================================================
# API EXCEPTIONS
# =============================================================================

class APIException(SetkaException):
    """Исключения API"""
    pass


class ValidationException(APIException):
    """Ошибка валидации данных"""
    pass


class NotFoundException(APIException):
    """Ресурс не найден"""
    def __init__(self, resource_type: str, resource_id: any):
        message = f"{resource_type} not found: {resource_id}"
        details = {
            "resource_type": resource_type,
            "resource_id": str(resource_id)
        }
        super().__init__(message, details)


class DuplicateException(APIException):
    """Дубликат ресурса"""
    def __init__(self, resource_type: str, field: str, value: any):
        message = f"{resource_type} with {field}={value} already exists"
        details = {
            "resource_type": resource_type,
            "field": field,
            "value": str(value)
        }
        super().__init__(message, details)


# =============================================================================
# VK API EXCEPTIONS
# =============================================================================

class VKAPIException(SetkaException):
    """Исключения VK API"""
    def __init__(
        self,
        message: str,
        error_code: int = None,
        retry_after: int = None,
        method: str = None
    ):
        self.error_code = error_code
        self.retry_after = retry_after
        self.method = method
        
        details = {
            "error_code": error_code,
            "retry_after": retry_after,
            "method": method
        }
        super().__init__(message, details)


class VKRateLimitException(VKAPIException):
    """Превышен лимит VK API (error code 6)"""
    def __init__(self, retry_after: int = 1):
        super().__init__(
            message="VK API rate limit exceeded",
            error_code=6,
            retry_after=retry_after
        )


class VKAccessDeniedException(VKAPIException):
    """Доступ запрещён (error code 15)"""
    def __init__(self, message: str = "Access denied"):
        super().__init__(message=message, error_code=15)


class VKTokenInvalidException(VKAPIException):
    """Невалидный токен (error code 5)"""
    def __init__(self):
        super().__init__(
            message="VK token is invalid or expired",
            error_code=5
        )


# =============================================================================
# DATABASE EXCEPTIONS
# =============================================================================

class DatabaseException(SetkaException):
    """Исключения базы данных"""
    pass


class DatabaseConnectionException(DatabaseException):
    """Ошибка подключения к БД"""
    pass


class DatabaseQueryException(DatabaseException):
    """Ошибка выполнения запроса"""
    def __init__(self, query: str, error: str):
        message = f"Database query failed: {error}"
        details = {"query": query[:200], "error": error}  # Limit query length
        super().__init__(message, details)


# =============================================================================
# CACHE EXCEPTIONS
# =============================================================================

class CacheException(SetkaException):
    """Исключения кэша"""
    pass


class CacheConnectionException(CacheException):
    """Ошибка подключения к Redis"""
    pass


# =============================================================================
# PROCESSING EXCEPTIONS
# =============================================================================

class ProcessingException(SetkaException):
    """Исключения обработки контента"""
    pass


class AnalysisException(ProcessingException):
    """Ошибка анализа поста"""
    def __init__(self, post_id: int, error: str):
        message = f"Failed to analyze post {post_id}: {error}"
        details = {"post_id": post_id, "error": error}
        super().__init__(message, details)


class PublishingException(ProcessingException):
    """Ошибка публикации"""
    def __init__(self, post_id: int, channel: str, error: str):
        message = f"Failed to publish post {post_id} to {channel}: {error}"
        details = {"post_id": post_id, "channel": channel, "error": error}
        super().__init__(message, details)


class DeduplicationException(ProcessingException):
    """Ошибка дедупликации"""
    pass


# =============================================================================
# CONFIGURATION EXCEPTIONS
# =============================================================================

class ConfigurationException(SetkaException):
    """Исключения конфигурации"""
    pass


class MissingConfigException(ConfigurationException):
    """Отсутствует обязательная конфигурация"""
    def __init__(self, config_key: str):
        message = f"Required configuration missing: {config_key}"
        details = {"config_key": config_key}
        super().__init__(message, details)


# =============================================================================
# EXTERNAL SERVICE EXCEPTIONS
# =============================================================================

class ExternalServiceException(SetkaException):
    """Исключения внешних сервисов"""
    pass


class GroqAPIException(ExternalServiceException):
    """Ошибка Groq API"""
    def __init__(self, message: str, status_code: int = None):
        details = {"status_code": status_code}
        super().__init__(message, details)


class TelegramAPIException(ExternalServiceException):
    """Ошибка Telegram API"""
    pass


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def handle_vk_error(error_code: int, error_msg: str, method: str = None):
    """
    Convert VK API error to appropriate exception
    
    Args:
        error_code: VK error code
        error_msg: Error message
        method: API method name
        
    Raises:
        Appropriate VKAPIException subclass
    """
    if error_code == 5:
        raise VKTokenInvalidException()
    elif error_code == 6:
        raise VKRateLimitException()
    elif error_code == 15:
        raise VKAccessDeniedException(error_msg)
    else:
        raise VKAPIException(error_msg, error_code=error_code, method=method)


if __name__ == "__main__":
    # Test exceptions
    print("Testing SETKA exceptions...")
    
    # Test basic exception
    try:
        raise SetkaException("Test error", {"key": "value"})
    except SetkaException as e:
        print(f"✅ SetkaException: {e.message}")
        print(f"   Details: {e.details}")
        print(f"   Dict: {e.to_dict()}")
    
    # Test VK exception
    try:
        raise VKRateLimitException(retry_after=5)
    except VKAPIException as e:
        print(f"\n✅ VKRateLimitException:")
        print(f"   Message: {e.message}")
        print(f"   Code: {e.error_code}")
        print(f"   Retry after: {e.retry_after}s")
    
    # Test not found exception
    try:
        raise NotFoundException("Region", "mi")
    except NotFoundException as e:
        print(f"\n✅ NotFoundException:")
        print(f"   Message: {e.message}")
        print(f"   Dict: {e.to_dict()}")
    
    print("\n✅ All exception tests passed!")

