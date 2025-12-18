"""
News Aggregator - умная агрегация новостей в дайджесты

Из Postopus LESSONS_LEARNED:
"Агрегация новостей в дайджест - одна из лучших находок!"
"Лучше один качественный дайджест, чем много отдельных постов"

Результаты Postopus:
- До агрегации: 5 постов × 200 просмотров = 1000
- После: 1 пост × 800 просмотров, НО лайки +40%, репосты +60%, жалобы -80%
"""
import logging
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class AggregatedPost:
    """
    Агрегированный пост (дайджест из нескольких новостей)
    """
    # Основной пост (якорь)
    anchor_post: Any
    
    # Дополнительные посты
    additional_posts: List[Any]
    
    # Сформированный текст
    aggregated_text: str
    
    # Общая статистика
    total_views: int
    total_likes: int
    total_reposts: int
    
    # Метаданные
    sources_count: int
    categories: List[str]
    
    def __str__(self):
        return f"<AggregatedPost anchor={self.anchor_post.id} +{len(self.additional_posts)} posts>"


class NewsAggregator:
    """
    Агрегирует несколько новостей в один дайджест
    
    Логика из Postopus:
    1. Берется первая новость (самая просматриваемая)
    2. Добавляется ее текст и медиа
    3. Если есть место - добавляются еще новости
    4. Проверка: текст < MAX_SIZE и медиа < 10
    5. Получается дайджест из 2-5 новостей
    """
    
    def __init__(
        self,
        max_posts_per_digest: int = 5,
        max_text_length: int = 4000,
        max_media_items: int = 10
    ):
        self.max_posts_per_digest = max_posts_per_digest
        self.max_text_length = max_text_length
        self.max_media_items = max_media_items
    
    async def aggregate(
        self,
        posts: List[Any],
        title: str = "📰 НОВОСТИ",
        hashtags: List[str] = None
    ) -> Optional[AggregatedPost]:
        """
        Агрегировать список постов в дайджест
        
        Args:
            posts: Список отсортированных постов (по просмотрам!)
            title: Заголовок дайджеста
            hashtags: Хештеги для добавления
            
        Returns:
            AggregatedPost или None если нечего агрегировать
        """
        if not posts:
            return None
        
        if len(posts) == 1:
            # Только один пост - возвращаем его как есть
            return AggregatedPost(
                anchor_post=posts[0],
                additional_posts=[],
                aggregated_text=self._format_single_post(posts[0], title, hashtags),
                total_views=getattr(posts[0], 'views', 0),
                total_likes=getattr(posts[0], 'likes', 0),
                total_reposts=getattr(posts[0], 'reposts', 0),
                sources_count=1,
                categories=[getattr(posts[0], 'ai_category', 'novost')]
            )
        
        # Агрегация нескольких постов
        logger.info(f"Aggregating {len(posts)} posts into digest...")
        
        # Якорь - первый пост (самый просматриваемый)
        anchor = posts[0]
        additional = []
        
        current_text_length = len(getattr(anchor, 'text', '') or '')
        current_media_count = self._count_media(anchor)
        
        # Добавляем остальные посты пока есть место
        for post in posts[1:]:
            if len(additional) >= self.max_posts_per_digest - 1:
                break
            
            post_text_length = len(getattr(post, 'text', '') or '')
            post_media_count = self._count_media(post)
            
            # Проверка лимитов
            if (current_text_length + post_text_length > self.max_text_length or
                current_media_count + post_media_count > self.max_media_items):
                logger.debug(f"Reached limits, stopping aggregation")
                break
            
            additional.append(post)
            current_text_length += post_text_length
            current_media_count += post_media_count
        
        # Формирование текста дайджеста
        aggregated_text = self._format_digest(anchor, additional, title, hashtags)
        
        # Статистика
        total_views = sum(getattr(p, 'views', 0) for p in [anchor] + additional)
        total_likes = sum(getattr(p, 'likes', 0) for p in [anchor] + additional)
        total_reposts = sum(getattr(p, 'reposts', 0) for p in [anchor] + additional)
        
        categories = list(set(
            getattr(p, 'ai_category', 'novost') 
            for p in [anchor] + additional
        ))
        
        result = AggregatedPost(
            anchor_post=anchor,
            additional_posts=additional,
            aggregated_text=aggregated_text,
            total_views=total_views,
            total_likes=total_likes,
            total_reposts=total_reposts,
            sources_count=len(additional) + 1,
            categories=categories
        )
        
        logger.info(f"Created digest: {result}")
        
        return result
    
    def _format_single_post(
        self,
        post: Any,
        title: str,
        hashtags: List[str] = None
    ) -> str:
        """Форматирование одного поста"""
        parts = []
        
        # Заголовок
        if title:
            parts.append(title)
            parts.append("")
        
        # Текст
        if hasattr(post, 'text') and post.text:
            parts.append(post.text)
        
        # Атрибуция источника
        attribution = self._format_attribution(post)
        if attribution:
            parts.append("")
            parts.append(attribution)
        
        # Хештеги
        if hashtags:
            parts.append("")
            parts.append(" ".join(hashtags))
        
        return "\n".join(parts)
    
    def _format_digest(
        self,
        anchor: Any,
        additional: List[Any],
        title: str,
        hashtags: List[str] = None
    ) -> str:
        """
        Форматирование дайджеста из нескольких постов
        
        Формат из Postopus:
        📰 НОВОСТИ
        
        {текст новости 1}
        @wall123_456 (Источник 1)
        
        {текст новости 2}
        @wall789_012 (Источник 2)
        
        #НовостиМалмыж
        """
        parts = []
        
        # Заголовок
        if title:
            parts.append(title)
            parts.append("")
        
        # Якорь (первая новость)
        if hasattr(anchor, 'text') and anchor.text:
            parts.append(anchor.text)
        
        # Атрибуция якоря
        attribution = self._format_attribution(anchor)
        if attribution:
            parts.append(attribution)
        
        # Дополнительные новости
        for post in additional:
            parts.append("")  # Пустая строка между новостями
            
            if hasattr(post, 'text') and post.text:
                parts.append(post.text)
            
            attribution = self._format_attribution(post)
            if attribution:
                parts.append(attribution)
        
        # Хештеги в конце
        if hashtags:
            parts.append("")
            parts.append(" ".join(hashtags))
        
        return "\n".join(parts)
    
    def _format_attribution(self, post: Any) -> str:
        """
        Форматирование атрибуции источника
        
        Из Postopus: "{ссылка} (Название Источника)"
        """
        if not hasattr(post, 'vk_owner_id') or not hasattr(post, 'vk_post_id'):
            return ""
        
        # VK ссылка
        link = f"@wall{post.vk_owner_id}_{post.vk_post_id}"
        
        # Название источника
        # TODO: Получать из БД или кэша
        source_name = "Источник"
        
        return f"{link} ({source_name})"
    
    def _count_media(self, post: Any) -> int:
        """Подсчитать количество медиа элементов"""
        if hasattr(post, 'fingerprint_media') and post.fingerprint_media:
            return len(post.fingerprint_media)
        elif hasattr(post, 'attachments') and post.attachments:
            if isinstance(post.attachments, list):
                return len(post.attachments)
            elif isinstance(post.attachments, dict):
                return len(post.attachments.get('items', []))
        return 0
    
    async def aggregate_by_category(
        self,
        posts: List[Any],
        max_digests: int = 3
    ) -> List[AggregatedPost]:
        """
        Агрегировать посты по категориям
        
        Создает отдельный дайджест для каждой категории
        
        Args:
            posts: Список постов (могут быть разных категорий)
            max_digests: Максимум дайджестов
            
        Returns:
            Список AggregatedPost
        """
        # Группируем по категориям
        by_category = {}
        for post in posts:
            category = getattr(post, 'ai_category', 'novost')
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(post)
        
        # Создаем дайджесты
        digests = []
        
        for category, category_posts in sorted(by_category.items(), key=lambda x: len(x[1]), reverse=True):
            if len(digests) >= max_digests:
                break
            
            # Сортируем по просмотрам
            category_posts.sort(key=lambda p: getattr(p, 'views', 0), reverse=True)
            
            # Определяем заголовок
            titles = {
                'novost': '📰 НОВОСТИ',
                'admin': '🏛️ ОФИЦИАЛЬНО',
                'kultura': '🎭 КУЛЬТУРА',
                'sport': '⚽ СПОРТ',
                'reklama': '📢 ОБЪЯВЛЕНИЯ'
            }
            title = titles.get(category, '📋 ВАЖНОЕ')
            
            digest = await self.aggregate(category_posts, title=title)
            if digest:
                digests.append(digest)
        
        logger.info(f"Created {len(digests)} category digests")
        
        return digests

