"""
Processing Context - управление состоянием обработки

Заменяет глобальный session словарь из Postopus на явный контекст.

Из Postopus LESSONS_LEARNED:
"Глобальная сессия - антипаттерн. Хорошо для маленькой системы,
 плохо для масштабирования. Нужно dependency injection."
"""
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class RegionContext:
    """
    Контекст обработки для конкретного региона
    
    Заменяет session['name_base'] и связанные параметры из Postopus
    """
    region_id: int
    region_code: str
    region_name: str
    
    # Конфигурация региона
    vk_target_group: Optional[int] = None
    telegram_channel: Optional[str] = None
    neighbors: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    
    # Метаданные
    is_neighbor_content: bool = False
    
    def __str__(self):
        return f"<RegionContext {self.region_code}: {self.region_name}>"


@dataclass
class ProcessingContext:
    """
    Контекст обработки контента
    
    Замена глобального session из Postopus.
    Содержит все необходимое для обработки без глобального состояния.
    
    Преимущества:
    - Явная передача зависимостей
    - Можно параллельно обрабатывать разные регионы
    - Легко тестировать
    - Immutable конфигурация
    """
    
    # Основная информация
    region: RegionContext
    content_type: str  # novost, reklama, kultura, etc.
    
    # База данных
    db_session: AsyncSession
    
    # Временные рабочие данные (mutable)
    work_data: Dict[str, Any] = field(default_factory=dict)
    
    # История обработки (для дедупликации)
    processed_lips: set = field(default_factory=set)
    processed_media_hashes: set = field(default_factory=set)
    processed_text_hashes: set = field(default_factory=set)
    
    # Метаданные обработки
    timestamp: datetime = field(default_factory=datetime.utcnow)
    
    def __str__(self):
        return f"<ProcessingContext {self.region.region_code}/{self.content_type}>"
    
    def add_processed_lip(self, lip: str):
        """Добавить обработанный LIP"""
        self.processed_lips.add(lip)
    
    def add_processed_media(self, media_hash: str):
        """Добавить обработанный медиа хеш"""
        self.processed_media_hashes.add(media_hash)
    
    def add_processed_text(self, text_hash: str):
        """Добавить обработанный текстовый хеш"""
        self.processed_text_hashes.add(text_hash)
    
    def is_duplicate_lip(self, lip: str) -> bool:
        """Проверить дубликат LIP"""
        return lip in self.processed_lips
    
    def is_duplicate_media(self, media_hash: str) -> bool:
        """Проверить дубликат медиа"""
        return media_hash in self.processed_media_hashes
    
    def is_duplicate_text(self, text_hash: str) -> bool:
        """Проверить дубликат текста"""
        return text_hash in self.processed_text_hashes
    
    def get_stats(self) -> Dict[str, Any]:
        """Получить статистику обработки"""
        return {
            'region': str(self.region),
            'content_type': self.content_type,
            'processed_lips': len(self.processed_lips),
            'processed_media': len(self.processed_media_hashes),
            'processed_texts': len(self.processed_text_hashes),
            'timestamp': self.timestamp.isoformat()
        }


class ContextManager:
    """
    Менеджер контекстов обработки
    
    Создает и управляет ProcessingContext для разных регионов/типов контента
    """
    
    def __init__(self):
        self._contexts: Dict[str, ProcessingContext] = {}
    
    async def create_context(
        self,
        region_id: int,
        region_code: str,
        region_name: str,
        content_type: str,
        db_session: AsyncSession,
        **kwargs
    ) -> ProcessingContext:
        """
        Создать новый контекст обработки
        
        Args:
            region_id: ID региона
            region_code: Код региона (mi, nolinsk, etc.)
            region_name: Название региона
            content_type: Тип контента (novost, reklama, etc.)
            db_session: Сессия БД
            **kwargs: Дополнительные параметры для RegionContext
            
        Returns:
            ProcessingContext готовый к использованию
        """
        region_ctx = RegionContext(
            region_id=region_id,
            region_code=region_code,
            region_name=region_name,
            **kwargs
        )
        
        context = ProcessingContext(
            region=region_ctx,
            content_type=content_type,
            db_session=db_session
        )
        
        # Сохранить для повторного использования
        key = f"{region_code}_{content_type}"
        self._contexts[key] = context
        
        logger.info(f"Created context: {context}")
        
        return context
    
    def get_context(self, region_code: str, content_type: str) -> Optional[ProcessingContext]:
        """Получить существующий контекст"""
        key = f"{region_code}_{content_type}"
        return self._contexts.get(key)
    
    def clear_contexts(self):
        """Очистить все контексты"""
        self._contexts.clear()

