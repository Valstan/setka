"""
VK Tokens Database Model
Модель для работы с токенами VK API в базе данных
"""
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, JSON
from sqlalchemy.sql import func
from database.base import Base


class VKToken(Base):
    """Модель токена VK API"""
    __tablename__ = "vk_tokens"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), unique=True, nullable=False, index=True)  # VALSTAN, OLGA, etc.
    token = Column(Text, nullable=False)  # VK API токен
    is_active = Column(Boolean, default=True, index=True)  # Активен ли токен
    last_used = Column(DateTime(timezone=True))  # Последнее использование
    last_validated = Column(DateTime(timezone=True))  # Последняя валидация
    validation_status = Column(String(20), default='unknown', index=True)  # valid, invalid, unknown
    error_message = Column(Text)  # Сообщение об ошибке
    permissions = Column(JSON)  # Права доступа токена
    user_info = Column(JSON)  # Информация о пользователе
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    def __repr__(self):
        return f"<VKToken(name='{self.name}', status='{self.validation_status}', active={self.is_active})>"
    
    def to_dict(self):
        """Преобразовать в словарь для API"""
        return {
            "id": self.id,
            "name": self.name,
            "token": self.token[:20] + "..." if len(self.token) > 20 else self.token,  # Маскируем токен
            "is_active": self.is_active,
            "last_used": self.last_used.isoformat() if self.last_used else None,
            "last_validated": self.last_validated.isoformat() if self.last_validated else None,
            "validation_status": self.validation_status,
            "error_message": self.error_message,
            "permissions": self.permissions,
            "user_info": self.user_info,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None
        }
    
    def get_full_token(self):
        """Получить полный токен (для внутреннего использования)"""
        return self.token
