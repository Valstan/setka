"""
Post Clustering - кластеризация похожих постов

Группирует посты по:
1. Временная близость (в течение часа)
2. Тематическая близость (ключевые слова)
3. Географическая близость (один регион)
4. Типологическая близость (одна категория)
"""
import logging
from typing import List, Dict, Any, Set
from datetime import timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)


class PostClusterer:
    """
    Кластеризация постов для умной агрегации
    """
    
    def __init__(
        self,
        time_window_hours: float = 6.0,
        min_cluster_size: int = 2
    ):
        self.time_window_hours = time_window_hours
        self.min_cluster_size = min_cluster_size
    
    async def cluster_posts(
        self,
        posts: List[Any],
        by_category: bool = True,
        by_time: bool = True
    ) -> List[List[Any]]:
        """
        Кластеризация постов
        
        Args:
            posts: Список постов для кластеризации
            by_category: Группировать по категории
            by_time: Группировать по времени
            
        Returns:
            Список кластеров (каждый кластер - список постов)
        """
        if not posts:
            return []
        
        logger.info(f"Clustering {len(posts)} posts...")
        
        clusters = []
        
        if by_category:
            # Группируем по категориям
            by_cat = defaultdict(list)
            for post in posts:
                category = getattr(post, 'ai_category', 'novost')
                by_cat[category].append(post)
            
            # Для каждой категории - временная кластеризация
            for category, cat_posts in by_cat.items():
                if by_time:
                    time_clusters = self._cluster_by_time(cat_posts)
                    clusters.extend(time_clusters)
                else:
                    if len(cat_posts) >= self.min_cluster_size:
                        clusters.append(cat_posts)
        else:
            # Только по времени
            if by_time:
                clusters = self._cluster_by_time(posts)
            else:
                clusters = [posts]
        
        # Фильтруем маленькие кластеры
        clusters = [c for c in clusters if len(c) >= self.min_cluster_size]
        
        logger.info(f"Created {len(clusters)} clusters")
        
        return clusters
    
    def _cluster_by_time(self, posts: List[Any]) -> List[List[Any]]:
        """
        Кластеризация по времени публикации
        
        Группирует посты в пределах time_window_hours
        """
        if not posts:
            return []
        
        # Сортируем по дате
        sorted_posts = sorted(
            posts,
            key=lambda p: getattr(p, 'date_published', None) or datetime.min,
            reverse=True
        )
        
        clusters = []
        current_cluster = [sorted_posts[0]]
        
        for post in sorted_posts[1:]:
            # Проверяем временную близость с первым постом кластера
            first_post = current_cluster[0]
            
            first_date = getattr(first_post, 'date_published', None)
            post_date = getattr(post, 'date_published', None)
            
            if first_date and post_date:
                time_diff = abs((first_date - post_date).total_seconds() / 3600)
                
                if time_diff <= self.time_window_hours:
                    # Добавляем в текущий кластер
                    current_cluster.append(post)
                else:
                    # Создаем новый кластер
                    if len(current_cluster) >= self.min_cluster_size:
                        clusters.append(current_cluster)
                    current_cluster = [post]
            else:
                current_cluster.append(post)
        
        # Добавляем последний кластер
        if len(current_cluster) >= self.min_cluster_size:
            clusters.append(current_cluster)
        
        return clusters
    
    async def cluster_by_similarity(
        self,
        posts: List[Any],
        similarity_threshold: float = 0.7
    ) -> List[List[Any]]:
        """
        Кластеризация по семантической близости
        
        Использует простое сравнение ключевых слов
        (для полноценного embeddings нужен AI)
        
        Args:
            posts: Список постов
            similarity_threshold: Порог схожести (0-1)
            
        Returns:
            Список кластеров
        """
        if not posts:
            return []
        
        logger.info(f"Clustering {len(posts)} posts by similarity...")
        
        # Извлекаем ключевые слова для каждого поста
        posts_keywords = []
        for post in posts:
            keywords = self._extract_keywords(post)
            posts_keywords.append((post, keywords))
        
        # Простая кластеризация на основе пересечения ключевых слов
        clusters = []
        used = set()
        
        for i, (post1, keywords1) in enumerate(posts_keywords):
            if i in used:
                continue
            
            cluster = [post1]
            used.add(i)
            
            for j, (post2, keywords2) in enumerate(posts_keywords[i+1:], i+1):
                if j in used:
                    continue
                
                # Рассчитываем Jaccard similarity
                similarity = self._jaccard_similarity(keywords1, keywords2)
                
                if similarity >= similarity_threshold:
                    cluster.append(post2)
                    used.add(j)
            
            if len(cluster) >= self.min_cluster_size:
                clusters.append(cluster)
        
        logger.info(f"Created {len(clusters)} similarity clusters")
        
        return clusters
    
    def _extract_keywords(self, post: Any) -> Set[str]:
        """Извлечь ключевые слова из поста"""
        if not hasattr(post, 'text') or not post.text:
            return set()
        
        import re
        
        # Простая экстракция: слова длиннее 4 символов
        words = re.findall(r'[а-яёА-ЯЁ]{4,}', post.text.lower())
        
        # Убираем стоп-слова
        stop_words = {
            'который', 'которая', 'которые', 'также', 'более',
            'этот', 'этого', 'сегодня', 'вчера', 'завтра'
        }
        
        keywords = set(w for w in words if w not in stop_words)
        
        return keywords
    
    def _jaccard_similarity(self, set1: Set[str], set2: Set[str]) -> float:
        """
        Рассчитать Jaccard similarity между двумя множествами
        
        Jaccard = |A ∩ B| / |A ∪ B|
        """
        if not set1 or not set2:
            return 0.0
        
        intersection = len(set1.intersection(set2))
        union = len(set1.union(set2))
        
        return intersection / union if union > 0 else 0.0

