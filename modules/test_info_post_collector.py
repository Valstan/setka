"""
Test-Info Post Collector - Сбор постов по тематическим группам для региона "Тест-Инфо"

Собирает посты из разных категорий групп региона "Тест-Инфо":
- admin: Администрация Малмыжского района
- kultura: МБУК Малмыжский районный Центр культуры и досуга  
- novost: МалмыЖ
- other: Малмыжский лицеист
- reklama: Малмыж Объявления, ОБЪЯВЛЕНИЯ МАЛМЫЖ
- test: Тестовый полигон (только для сравнения, чтобы избежать повторов)
"""
import logging
from typing import Dict, List, Any, Optional
from datetime import datetime
import pytz

from modules.vk_monitor.vk_client import VKClient
from database.connection import AsyncSessionLocal
from sqlalchemy import text

logger = logging.getLogger(__name__)


class TestInfoPostCollector:
    """Сборщик постов для региона Тест-Инфо по тематическим группам"""
    
    def __init__(self, vk_token: str):
        self.vk_token = vk_token
        self.vk_client = VKClient(vk_token)
        self.region_id = None  # Будет загружен из БД
        
    async def load_region_communities(self) -> Dict[str, List[Dict]]:
        """Загрузить группы региона Тест-Инфо из базы данных"""
        async with AsyncSessionLocal() as session:
            # Получаем ID региона Тест-Инфо
            result = await session.execute(text('''
                SELECT id FROM regions WHERE code = 'test'
            '''))
            region_row = result.fetchone()
            
            if not region_row:
                logger.error("❌ Регион 'test' не найден в базе данных")
                return {}
            
            self.region_id = region_row[0]
            
            # Получаем все группы региона
            result = await session.execute(text('''
                SELECT vk_id, name, category, screen_name
                FROM communities 
                WHERE region_id = :region_id AND is_active = true
                ORDER BY category, name
            '''), {'region_id': self.region_id})
            
            communities = result.fetchall()
            
            # Группируем по категориям
            communities_by_category = {}
            for comm in communities:
                category = comm[2]  # category
                if category not in communities_by_category:
                    communities_by_category[category] = []
                
                communities_by_category[category].append({
                    'vk_id': comm[0],
                    'name': comm[1],
                    'category': comm[2],
                    'screen_name': comm[3]
                })
            
            logger.info(f"📋 Загружено групп для Тест-Инфо: {len(communities)} в {len(communities_by_category)} категориях")
            for category, comms in communities_by_category.items():
                logger.info(f"  {category}: {len(comms)} групп")
            
            return communities_by_category
    
    async def collect_posts_by_topic(self, topic: str, communities_by_category: Dict[str, List[Dict]]) -> List[Dict]:
        """
        Собрать посты по теме из соответствующих категорий групп
        
        Args:
            topic: Тема (Администрация, Культура, Спорт, Новости, События, Образование, Здоровье, Бизнес)
            communities_by_category: Группы, сгруппированные по категориям
        
        Returns:
            Список постов
        """
        # Маппинг тем на категории групп
        topic_to_categories = {
            'Администрация': ['admin'],
            'Культура': ['kultura'],
            'Спорт': ['sport'],  # Если есть спортивные группы
            'Новости': ['novost'],
            'События': ['kultura', 'novost'],
            'Образование': ['other'],  # Малмыжский лицеист
            'Здоровье': ['other', 'novost'],
            'Бизнес': ['reklama', 'novost']
        }
        
        # Получаем категории для текущей темы
        target_categories = topic_to_categories.get(topic, ['novost'])  # По умолчанию новости
        
        logger.info(f"🎯 Собираем посты по теме '{topic}' из категорий: {target_categories}")
        
        all_posts = []
        
        for category in target_categories:
            if category not in communities_by_category:
                logger.warning(f"⚠️ Категория '{category}' не найдена для темы '{topic}'")
                continue
            
            communities = communities_by_category[category]
            logger.info(f"📡 Собираем из категории '{category}': {len(communities)} групп")
            
            for community in communities:
                try:
                    vk_id = community['vk_id']
                    name = community['name']
                    
                    logger.info(f"  📥 Собираем посты из группы: {name} (ID: {vk_id})")
                    
                    # Собираем посты из группы
                    posts = self.vk_client.get_wall_posts(vk_id, count=10)
                    
                    # Добавляем метаданные к постам
                    for post in posts:
                        post['source_community'] = name
                        post['source_category'] = category
                        post['source_vk_id'] = vk_id
                    
                    all_posts.extend(posts)
                    logger.info(f"    ✅ Получено {len(posts)} постов")
                    
                except Exception as e:
                    logger.error(f"    ❌ Ошибка при сборе постов из {name}: {e}")
                    continue
        
        logger.info(f"📊 Всего собрано постов по теме '{topic}': {len(all_posts)}")
        return all_posts
    
    async def collect_comparison_posts(self, communities_by_category: Dict[str, List[Dict]]) -> List[Dict]:
        """
        Собрать посты из главной группы для сравнения (чтобы избежать повторов)
        
        Args:
            communities_by_category: Группы, сгруппированные по категориям
        
        Returns:
            Список постов из главной группы
        """
        # Берем посты только из группы "test" (Тестовый полигон) для сравнения
        test_communities = communities_by_category.get('test', [])
        
        if not test_communities:
            logger.warning("⚠️ Главная группа 'test' не найдена для сравнения")
            return []
        
        comparison_posts = []
        
        for community in test_communities:
            try:
                vk_id = community['vk_id']
                name = community['name']
                
                logger.info(f"🔍 Собираем посты для сравнения из главной группы: {name} (ID: {vk_id})")
                
                posts = self.vk_client.get_wall_posts(vk_id, count=20)  # Больше постов для сравнения
                
                # Добавляем метаданные
                for post in posts:
                    post['source_community'] = name
                    post['source_category'] = 'test'
                    post['source_vk_id'] = vk_id
                    post['is_comparison'] = True  # Помечаем как посты для сравнения
                
                comparison_posts.extend(posts)
                logger.info(f"    ✅ Получено {len(posts)} постов для сравнения")
                
            except Exception as e:
                logger.error(f"    ❌ Ошибка при сборе постов для сравнения из {name}: {e}")
                continue
        
        logger.info(f"🔍 Всего постов для сравнения: {len(comparison_posts)}")
        return comparison_posts
    
    def filter_duplicates(self, topic_posts: List[Dict], comparison_posts: List[Dict]) -> List[Dict]:
        """
        Удалить дубликаты постов, сравнивая с главной группой
        
        Args:
            topic_posts: Посты по теме
            comparison_posts: Посты из главной группы для сравнения
        
        Returns:
            Отфильтрованные посты без дубликатов
        """
        if not comparison_posts:
            logger.info("🔍 Нет постов для сравнения, возвращаем все посты по теме")
            return topic_posts
        
        # Создаем множество текстов постов из главной группы для быстрого поиска
        comparison_texts = set()
        for post in comparison_posts:
            text = post.get('text', '').strip()
            if text:
                # Нормализуем текст для сравнения (убираем лишние пробелы, приводим к нижнему регистру)
                normalized_text = ' '.join(text.lower().split())
                comparison_texts.add(normalized_text)
        
        logger.info(f"🔍 Создан индекс из {len(comparison_texts)} постов для сравнения")
        
        # Фильтруем посты по теме
        filtered_posts = []
        duplicates_count = 0
        
        for post in topic_posts:
            text = post.get('text', '').strip()
            if text:
                normalized_text = ' '.join(text.lower().split())
                
                if normalized_text in comparison_texts:
                    duplicates_count += 1
                    logger.debug(f"🔄 Найден дубликат: {text[:50]}...")
                else:
                    filtered_posts.append(post)
        
        logger.info(f"✅ Отфильтровано дубликатов: {duplicates_count}")
        logger.info(f"📊 Осталось уникальных постов: {len(filtered_posts)}")
        
        return filtered_posts


async def collect_test_info_posts_by_topic(vk_token: str, topic: str) -> Dict[str, Any]:
    """
    Главная функция для сбора постов по теме для региона Тест-Инфо
    
    Args:
        vk_token: VK API токен
        topic: Тема для сбора постов
    
    Returns:
        Результат сбора постов
    """
    logger.info(f"🚀 Начинаем сбор постов по теме '{topic}' для Тест-Инфо")
    
    try:
        collector = TestInfoPostCollector(vk_token)
        
        # Загружаем группы региона
        communities_by_category = await collector.load_region_communities()
        
        if not communities_by_category:
            return {
                'success': False,
                'error': 'Не удалось загрузить группы региона Тест-Инфо',
                'topic': topic,
                'posts_collected': 0
            }
        
        # Собираем посты по теме
        topic_posts = await collector.collect_posts_by_topic(topic, communities_by_category)
        
        # Собираем посты для сравнения
        comparison_posts = await collector.collect_comparison_posts(communities_by_category)
        
        # Фильтруем дубликаты
        filtered_posts = collector.filter_duplicates(topic_posts, comparison_posts)
        
        # Статистика по категориям
        category_stats = {}
        for post in filtered_posts:
            category = post.get('source_category', 'unknown')
            category_stats[category] = category_stats.get(category, 0) + 1
        
        logger.info(f"✅ Сбор постов по теме '{topic}' завершен")
        logger.info(f"📊 Статистика по категориям: {category_stats}")
        
        return {
            'success': True,
            'topic': topic,
            'posts_collected': len(filtered_posts),
            'posts_before_filtering': len(topic_posts),
            'comparison_posts': len(comparison_posts),
            'duplicates_filtered': len(topic_posts) - len(filtered_posts),
            'category_stats': category_stats,
            'posts': filtered_posts[:10],  # Возвращаем только первые 10 для логирования
            'timestamp': datetime.now(pytz.timezone('Europe/Moscow')).isoformat()
        }
        
    except Exception as e:
        logger.error(f"❌ Ошибка при сборе постов по теме '{topic}': {e}")
        return {
            'success': False,
            'error': str(e),
            'topic': topic,
            'posts_collected': 0,
            'timestamp': datetime.now(pytz.timezone('Europe/Moscow')).isoformat()
        }
