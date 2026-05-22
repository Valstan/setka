"""
Pydantic модели для конфигураций

Заменяет словари конфигураций из Postopus на type-safe модели
"""

import logging
from typing import TYPE_CHECKING, Dict, List, Optional

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from modules.core.context import ProcessingContext  # noqa: F401

logger = logging.getLogger(__name__)


class ContentTypeConfig(BaseModel):
    """
    Конфигурация типа контента (novost, reklama, etc.)

    Из Postopus: session['name_session'] определял тип контента
    """

    name: str  # novost, reklama, kultura, sport, etc.
    title: str  # "📰 НОВОСТИ", "🎭 КУЛЬТУРА", etc.
    hashtags: List[str] = Field(default_factory=list)

    # Лимиты контента
    max_posts_per_digest: int = 5  # Максимум постов в дайджесте
    max_text_length: int = 4000  # Максимум символов
    max_media_items: int = 10  # Максимум медиа элементов

    # Настройки фильтрации
    min_views: int = 0
    min_words: int = 3
    max_age_hours: int = 72

    # Приоритет (для сортировки)
    priority: int = 50

    class Config:
        frozen = True  # Immutable


class SourceConfig(BaseModel):
    """
    Конфигурация источника контента (VK сообщество)

    Из Postopus: каждая категория содержала {название: vk_id}
    """

    vk_id: int
    name: str
    category: str  # admin, novost, kultura, etc.
    priority: int = 50  # Приоритет источника (репутация)
    is_active: bool = True

    # Специальные правила
    only_from_admin: bool = False  # Только от админа группы
    skip_with_links: bool = False  # Пропускать с ссылками на сайт

    class Config:
        frozen = True


class FilterConfig(BaseModel):
    """
    Конфигурация фильтра
    """

    type: str  # blacklist_word, black_id, etc.
    pattern: str
    action: str  # delete, clean, skip_attribution
    score_modifier: int = 0
    description: Optional[str] = None
    is_active: bool = True

    class Config:
        frozen = True


class RegionConfig(BaseModel):
    """
    Полная конфигурация региона

    Заменяет региональные коллекции MongoDB из Postopus
    на type-safe Pydantic модель

    Из Postopus: каждая региональная коллекция содержала:
    - config документ с настройками
    - таблицы для каждого типа контента (novost, reklama, etc.)
    """

    # Основная информация
    code: str  # mi, nolinsk, arbazh, etc.
    name: str  # "МАЛМЫЖ - ИНФО"

    # Публикация
    vk_target_group: Optional[int] = None  # Куда публиковать
    telegram_channel: Optional[str] = None  # Telegram канал

    # География
    neighbors: List[str] = Field(default_factory=list)  # Соседние районы
    keywords: List[str] = Field(default_factory=list)  # Региональные ключевые слова

    # Источники контента по категориям
    sources: Dict[str, List[SourceConfig]] = Field(default_factory=dict)

    # Настройки контента
    content_types: Dict[str, ContentTypeConfig] = Field(default_factory=dict)

    # Локальные фильтры
    local_filters: List[FilterConfig] = Field(default_factory=list)

    # Статус
    is_active: bool = True

    class Config:
        frozen = True  # Immutable

    def get_all_sources(self) -> List[SourceConfig]:
        """Получить все источники всех категорий"""
        all_sources = []
        for sources_list in self.sources.values():
            all_sources.extend(sources_list)
        return all_sources

    def get_sources_by_category(self, category: str) -> List[SourceConfig]:
        """Получить источники конкретной категории"""
        return self.sources.get(category, [])

    def get_active_sources(self) -> List[SourceConfig]:
        """Получить только активные источники"""
        return [s for s in self.get_all_sources() if s.is_active]


class ScheduleConfig(BaseModel):
    """
    Конфигурация расписания публикаций

    Из Postopus: CRON_SCHEDULE = {'task_name': 'minute hour category'}
    """

    region_code: str
    content_type: str

    # Расписание (cron-like)
    hour: List[int] = Field(default_factory=list)  # Часы публикации
    minute: int = 0  # Минута публикации

    # Или полное cron выражение
    cron_expression: Optional[str] = None

    # Метаданные
    description: Optional[str] = None
    is_active: bool = True

    class Config:
        frozen = True


class GlobalConfig(BaseModel):
    """
    Глобальная конфигурация системы

    Из Postopus: config коллекция с общими настройками
    """

    # Фильтры
    global_blacklist: List[str] = Field(default_factory=list)
    global_black_ids: List[int] = Field(default_factory=list)
    bad_name_groups: List[str] = Field(default_factory=list)

    # Региональные слова
    kirov_words: List[str] = Field(default_factory=list)
    tatar_words: List[str] = Field(default_factory=list)

    # Настройки обработки
    default_max_age_hours: int = 72
    default_min_views: int = 0

    # Лимиты
    max_posts_per_run: int = 50
    api_request_delay: float = 1.0  # Задержка между запросами

    class Config:
        frozen = True

    @classmethod
    def load_defaults(cls) -> "GlobalConfig":
        """Загрузить конфигурацию по умолчанию"""
        return cls(
            global_blacklist=[],
            global_black_ids=[],
            default_max_age_hours=72,
            default_min_views=0,
            max_posts_per_run=50,
            api_request_delay=1.0,
        )


# Фабрика для создания контекстов из БД
class ContextFactory:
    """
    Фабрика для создания ProcessingContext из данных БД
    """

    @staticmethod
    async def create_from_region(
        region_id: int, content_type: str, db_session: "AsyncSession"
    ) -> "ProcessingContext":
        """
        Создать ProcessingContext из данных региона в БД

        Args:
            region_id: ID региона
            content_type: Тип контента
            db_session: Сессия БД

        Returns:
            ProcessingContext готовый к использованию
        """
        from sqlalchemy import select

        from database.models import Region

        # Загрузить регион
        result = await db_session.execute(select(Region).where(Region.id == region_id))
        region = result.scalar_one_or_none()

        if not region:
            raise ValueError(f"Region {region_id} not found")

        # Создать RegionContext
        from modules.core.context import ProcessingContext, RegionContext

        neighbors = region.neighbors.split(",") if region.neighbors else []
        neighbors = [n.strip() for n in neighbors]

        region_ctx = RegionContext(
            region_id=region.id,
            region_code=region.code,
            region_name=region.name,
            vk_target_group=region.vk_group_id,
            telegram_channel=region.telegram_channel,
            neighbors=neighbors,
            keywords=[],  # TODO: Load from DB
        )

        # Создать ProcessingContext
        context = ProcessingContext(
            region=region_ctx, content_type=content_type, db_session=db_session
        )

        logger.info(f"Created context for {region.code}/{content_type}")

        return context
