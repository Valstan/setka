"""
Content Mixer - умное смешивание категорий контента
Создаёт сбалансированные дайджесты с оптимальным миксом новостей
"""
import logging
from typing import List, Dict
from collections import defaultdict
import random

logger = logging.getLogger(__name__)


class ContentMixer:
    """
    Умное смешивание контента для создания engaging дайджестов
    
    Правила:
    1. Разнообразие категорий (не все новости одной категории)
    2. Оптимальный порядок (чередование категорий)
    3. Сильное начало и конец (highest score)
    4. Баланс sentiment (не все negative)
    """
    
    # Оптимальный микс по времени суток
    OPTIMAL_MIX = {
        'morning': {
            'novost': 0.40,
            'admin': 0.20,
            'kultura': 0.15,
            'sport': 0.15,
            'sosed': 0.10
        },
        'afternoon': {
            'novost': 0.35,
            'admin': 0.15,
            'kultura': 0.20,
            'sport': 0.20,
            'sosed': 0.10
        },
        'evening': {
            'novost': 0.30,
            'admin': 0.10,
            'kultura': 0.25,
            'sport': 0.25,
            'sosed': 0.10
        }
    }
    
    # Оптимальный sentiment микс
    OPTIMAL_SENTIMENT_MIX = {
        'positive': 0.50,  # 50% позитивных
        'neutral': 0.30,   # 30% нейтральных
        'negative': 0.20   # 20% негативных
    }
    
    def create_balanced_digest(
        self,
        posts: List,
        max_posts: int = 10,
        time_slot: str = 'afternoon'
    ) -> List:
        """
        Создать сбалансированный дайджест
        
        Args:
            posts: Список доступных постов
            max_posts: Максимум постов в дайджесте
            time_slot: Время суток ('morning', 'afternoon', 'evening')
            
        Returns:
            Отсортированный список постов для дайджеста
        """
        if not posts:
            return []
        
        logger.info(f"Creating balanced digest from {len(posts)} posts (max: {max_posts}, slot: {time_slot})")
        
        # Группировать по категориям
        by_category = self._group_by_category(posts)
        
        # Получить целевой микс
        target_mix = self.OPTIMAL_MIX.get(time_slot, self.OPTIMAL_MIX['afternoon'])
        
        # Выбрать посты согласно миксу
        selected = self._select_by_mix(by_category, target_mix, max_posts)
        
        # Балансировать sentiment
        balanced = self._balance_sentiment(selected)
        
        # Оптимизировать порядок
        optimized = self._optimize_order(balanced)
        
        logger.info(f"Created digest with {len(optimized)} posts")
        
        return optimized
    
    def _group_by_category(self, posts: List) -> Dict[str, List]:
        """Группировать посты по категориям"""
        by_category = defaultdict(list)
        
        for post in posts:
            category = post.ai_category or 'novost'
            by_category[category].append(post)
        
        # Сортировать внутри категории по score
        for category in by_category:
            by_category[category].sort(key=lambda p: p.ai_score or 0, reverse=True)
        
        return dict(by_category)
    
    def _select_by_mix(
        self,
        by_category: Dict[str, List],
        target_mix: Dict[str, float],
        max_posts: int
    ) -> List:
        """
        Выбрать посты согласно целевому миксу
        
        Args:
            by_category: Посты сгруппированные по категориям
            target_mix: Целевые пропорции категорий
            max_posts: Максимум постов
            
        Returns:
            Список выбранных постов
        """
        selected = []
        
        for category, ratio in sorted(target_mix.items(), key=lambda x: x[1], reverse=True):
            count_needed = int(max_posts * ratio)
            
            posts_in_category = by_category.get(category, [])
            
            # Взять топ N постов из категории
            selected.extend(posts_in_category[:count_needed])
        
        # Если не набрали max_posts, добавить лучшие из оставшихся
        if len(selected) < max_posts:
            all_posts = [p for posts in by_category.values() for p in posts]
            remaining = [p for p in all_posts if p not in selected]
            remaining.sort(key=lambda p: p.ai_score or 0, reverse=True)
            
            selected.extend(remaining[:max_posts - len(selected)])
        
        return selected[:max_posts]
    
    def _balance_sentiment(self, posts: List) -> List:
        """
        Балансировать sentiment
        
        Избегает слишком много негативных новостей подряд
        """
        # Группировать по sentiment
        by_sentiment = defaultdict(list)
        
        for post in posts:
            sentiment = getattr(post, 'sentiment_label', 'neutral') or 'neutral'
            by_sentiment[sentiment].append(post)
        
        # Проверить пропорции
        total = len(posts)
        negative_pct = len(by_sentiment['negative']) / total if total > 0 else 0
        
        # Если слишком много негатива (>30%), заменить на позитив/нейтрал
        if negative_pct > 0.30:
            logger.warning(f"Too many negative posts ({negative_pct:.1%}), rebalancing...")
            
            # Оставить только лучшие negative (топ 20%)
            target_negative = int(total * 0.20)
            by_sentiment['negative'].sort(key=lambda p: p.ai_score or 0, reverse=True)
            keep_negative = by_sentiment['negative'][:target_negative]
            
            # Заменить остальные на positive/neutral
            replace_count = len(by_sentiment['negative']) - target_negative
            replacements = by_sentiment['positive'][:replace_count] if by_sentiment['positive'] else []
            replacements += by_sentiment['neutral'][:max(0, replace_count - len(replacements))]
            
            # Собрать сбалансированный список
            return keep_negative + by_sentiment['positive'] + by_sentiment['neutral'] + replacements
        
        return posts
    
    def _optimize_order(self, posts: List) -> List:
        """
        Оптимизировать порядок постов
        
        Правила:
        1. Начать с самого сильного поста (highest score)
        2. Чередовать категории
        3. Не ставить похожие sentiment рядом
        4. Закончить сильным постом
        """
        if len(posts) <= 2:
            return sorted(posts, key=lambda p: p.ai_score or 0, reverse=True)
        
        # Сортировать по score
        sorted_posts = sorted(posts, key=lambda p: p.ai_score or 0, reverse=True)
        
        # Начать с лучшего
        ordered = [sorted_posts[0]]
        remaining = sorted_posts[1:]
        
        while remaining:
            last_post = ordered[-1]
            
            # Найти наиболее "разный" пост
            next_post = self._find_most_different(last_post, remaining)
            
            ordered.append(next_post)
            remaining.remove(next_post)
        
        return ordered
    
    def _find_most_different(self, reference_post, candidates: List):
        """
        Найти пост, наиболее отличающийся от reference
        
        Отличия по:
        - Категория
        - Sentiment
        - Score (но не слишком низкий)
        """
        if not candidates:
            return None
        
        ref_category = reference_post.ai_category or 'novost'
        ref_sentiment = getattr(reference_post, 'sentiment_label', 'neutral')
        
        # Скоринг различия
        scores = []
        for post in candidates:
            score = 0
            
            # Разная категория +2
            if post.ai_category != ref_category:
                score += 2
            
            # Разный sentiment +1
            if getattr(post, 'sentiment_label', 'neutral') != ref_sentiment:
                score += 1
            
            # Бонус за качество поста
            score += (post.ai_score or 0) / 100
            
            scores.append((post, score))
        
        # Выбрать с максимальным score
        return max(scores, key=lambda x: x[1])[0]
    
    def get_digest_stats(self, posts: List) -> Dict:
        """
        Статистика дайджеста
        
        Args:
            posts: Список постов в дайджесте
            
        Returns:
            Dict со статистикой
        """
        # Категории
        categories = defaultdict(int)
        for post in posts:
            cat = post.ai_category or 'novost'
            categories[cat] += 1
        
        # Sentiment
        sentiments = defaultdict(int)
        for post in posts:
            sent = getattr(post, 'sentiment_label', 'neutral') or 'neutral'
            sentiments[sent] += 1
        
        # Quality
        avg_score = sum(p.ai_score or 0 for p in posts) / len(posts) if posts else 0
        
        return {
            'total_posts': len(posts),
            'categories': dict(categories),
            'sentiment_distribution': dict(sentiments),
            'average_score': round(avg_score, 1),
            'diversity_score': len(categories) / len(posts) if posts else 0
        }


if __name__ == "__main__":
    # Test
    from dataclasses import dataclass
    
    @dataclass
    class MockPost:
        ai_category: str
        ai_score: int
        sentiment_label: str
    
    print("="*60)
    print("🧪 Testing Content Mixer")
    print("="*60)
    
    # Create test posts
    test_posts = [
        MockPost('novost', 85, 'positive'),
        MockPost('novost', 75, 'neutral'),
        MockPost('admin', 70, 'neutral'),
        MockPost('sport', 80, 'positive'),
        MockPost('kultura', 65, 'positive'),
        MockPost('novost', 60, 'negative'),
        MockPost('admin', 55, 'neutral'),
        MockPost('sport', 72, 'positive'),
    ]
    
    mixer = ContentMixer()
    
    # Test balanced digest
    print("\n1. Creating balanced digest (max 5 posts)...")
    digest = mixer.create_balanced_digest(test_posts, max_posts=5, time_slot='afternoon')
    
    print(f"\n   Selected {len(digest)} posts:")
    for i, post in enumerate(digest, 1):
        print(f"   {i}. {post.ai_category} (score: {post.ai_score}, sentiment: {post.sentiment_label})")
    
    # Test stats
    print("\n2. Digest stats:")
    stats = mixer.get_digest_stats(digest)
    print(f"   Total: {stats['total_posts']}")
    print(f"   Categories: {stats['categories']}")
    print(f"   Sentiment: {stats['sentiment_distribution']}")
    print(f"   Avg score: {stats['average_score']}")
    print(f"   Diversity: {stats['diversity_score']:.2f}")
    
    print("\n✅ Test completed!")

