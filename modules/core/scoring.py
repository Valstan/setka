"""
Система scoring (оценки) постов

Улучшенная формула на основе опыта Postopus:
"Просмотры VK - самый важный сигнал! VK уже показал что люди хотят читать."
"""
import logging
from typing import Any, Dict, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class PostScorer:
    """
    Рассчитывает score (оценку) поста на основе множества факторов
    
    Из Postopus LESSONS_LEARNED:
    "Приоритизация по просмотрам работает отлично!"
    "Используй существующие сигналы платформы вместо изобретения своих метрик"
    
    Новая формула (по опыту Postopus):
    - Engagement (VK metrics): 40% (было 30%)
    - AI Relevance: 30% (было 50%)
    - Recency: 20% (было 20%)
    - Source Reputation: 10% (НОВОЕ!)
    """
    
    def __init__(
        self,
        engagement_weight: float = 0.4,
        relevance_weight: float = 0.3,
        recency_weight: float = 0.2,
        source_weight: float = 0.1
    ):
        self.engagement_weight = engagement_weight
        self.relevance_weight = relevance_weight
        self.recency_weight = recency_weight
        self.source_weight = source_weight
    
    def calculate_score(
        self,
        post: Any,
        ai_relevance: int = 50,
        source_priority: int = 50
    ) -> int:
        """
        Рассчитать итоговую оценку поста
        
        Args:
            post: Post объект или dict с полями views, likes, reposts, date_published
            ai_relevance: Релевантность от AI (0-100)
            source_priority: Приоритет источника (0-100)
            
        Returns:
            Итоговая оценка (0-100)
        """
        # Компоненты оценки
        engagement = self._calculate_engagement_score(post)
        recency = self._calculate_recency_score(post)
        
        # Общая формула
        total_score = (
            engagement * self.engagement_weight +
            ai_relevance * self.relevance_weight +
            recency * self.recency_weight +
            source_priority * self.source_weight
        )
        
        return int(min(max(total_score, 0), 100))
    
    def _calculate_engagement_score(self, post: Any) -> float:
        """
        Рассчитать engagement score (0-100)
        
        Из Postopus: Просмотры важнее всего!
        Лайки и репосты - бонусы
        """
        views = getattr(post, 'views', 0) or 0
        likes = getattr(post, 'likes', 0) or 0
        reposts = getattr(post, 'reposts', 0) or 0
        comments = getattr(post, 'comments', 0) or 0
        
        # Базовая оценка от просмотров (до 60 баллов)
        # Логарифмическая шкала для нелинейного роста
        import math
        if views > 0:
            views_score = min(math.log10(views + 1) * 20, 60)
        else:
            views_score = 0
        
        # Лайки (до 20 баллов)
        likes_score = min((likes / 10) * 20, 20)
        
        # Репосты (до 15 баллов) - очень ценны!
        reposts_score = min((reposts / 3) * 15, 15)
        
        # Комментарии (до 5 баллов)
        comments_score = min((comments / 5) * 5, 5)
        
        total = views_score + likes_score + reposts_score + comments_score
        
        # Бонусы за вирусность
        if views > 500:
            total *= 1.2  # +20% за высокие просмотры
        if reposts > 10:
            total *= 1.1  # +10% за активные репосты
        
        return min(total, 100)
    
    def _calculate_recency_score(self, post: Any) -> float:
        """
        Рассчитать recency score (0-100)
        
        Свежий контент важнее
        """
        if not hasattr(post, 'date_published') or not post.date_published:
            return 50  # Средняя оценка если нет даты
        
        age_seconds = (datetime.utcnow() - post.date_published).total_seconds()
        age_hours = age_seconds / 3600
        
        # Экспоненциальный спад
        if age_hours < 3:
            return 100  # Супер свежий (< 3 часов)
        elif age_hours < 6:
            return 95
        elif age_hours < 12:
            return 85
        elif age_hours < 24:
            return 70
        elif age_hours < 48:
            return 50
        elif age_hours < 72:
            return 30
        else:
            return 10  # Старый контент
    
    def get_score_breakdown(
        self,
        post: Any,
        ai_relevance: int = 50,
        source_priority: int = 50
    ) -> Dict[str, Any]:
        """
        Получить детальную разбивку оценки (для отладки)
        
        Returns:
            Словарь с компонентами оценки
        """
        engagement = self._calculate_engagement_score(post)
        recency = self._calculate_recency_score(post)
        
        total = self.calculate_score(post, ai_relevance, source_priority)
        
        return {
            'total_score': total,
            'components': {
                'engagement': {
                    'value': engagement,
                    'weight': self.engagement_weight,
                    'contribution': engagement * self.engagement_weight
                },
                'ai_relevance': {
                    'value': ai_relevance,
                    'weight': self.relevance_weight,
                    'contribution': ai_relevance * self.relevance_weight
                },
                'recency': {
                    'value': recency,
                    'weight': self.recency_weight,
                    'contribution': recency * self.recency_weight
                },
                'source': {
                    'value': source_priority,
                    'weight': self.source_weight,
                    'contribution': source_priority * self.source_weight
                }
            },
            'post_stats': {
                'views': getattr(post, 'views', 0),
                'likes': getattr(post, 'likes', 0),
                'reposts': getattr(post, 'reposts', 0),
                'comments': getattr(post, 'comments', 0)
            }
        }


# Предустановленные конфигурации
class ScorerPresets:
    """Предустановленные конфигурации scorer'а"""
    
    @staticmethod
    def postopus_style() -> PostScorer:
        """
        Scorer по образцу Postopus
        Акцент на engagement (просмотры VK)
        """
        return PostScorer(
            engagement_weight=0.4,
            relevance_weight=0.3,
            recency_weight=0.2,
            source_weight=0.1
        )
    
    @staticmethod
    def ai_heavy() -> PostScorer:
        """
        Scorer с акцентом на AI
        Для тестирования новых AI моделей
        """
        return PostScorer(
            engagement_weight=0.2,
            relevance_weight=0.5,
            recency_weight=0.2,
            source_weight=0.1
        )
    
    @staticmethod
    def viral_hunter() -> PostScorer:
        """
        Scorer для поиска вирусного контента
        Максимальный вес engagement
        """
        return PostScorer(
            engagement_weight=0.6,
            relevance_weight=0.1,
            recency_weight=0.2,
            source_weight=0.1
        )


# Convenience function для простого использования
def calculate_post_score(
    views: int = 0,
    likes: int = 0,
    reposts: int = 0,
    comments: int = 0,
    posted_at: Optional[datetime] = None,
    source_priority: float = 1.0,
    ai_category_weight: float = 0.8
) -> int:
    """
    Удобная функция для расчета score поста
    
    Args:
        views: Количество просмотров
        likes: Количество лайков
        reposts: Количество репостов
        comments: Количество комментариев
        posted_at: Дата публикации
        source_priority: Приоритет источника (0-1)
        ai_category_weight: Вес категории AI (0-1)
        
    Returns:
        Score поста (0-100)
    """
    # Создаем временный объект с нужными атрибутами
    class TempPost:
        pass
    
    post = TempPost()
    post.views = views
    post.likes = likes
    post.reposts = reposts
    post.comments = comments
    post.date_published = posted_at
    
    # Используем Postopus style scorer
    scorer = ScorerPresets.postopus_style()
    
    # AI relevance основан на весе категории
    ai_relevance = int(ai_category_weight * 100)
    
    # Source priority в проценты
    source_priority_score = int(source_priority * 100)
    
    return scorer.calculate_score(post, ai_relevance, source_priority_score)

