"""
Real VK Workflow Tasks
Реальные задачи для работы с VK API
"""
import logging
from datetime import datetime
from typing import Dict, List, Any
import pytz

from celery_app import app
from modules.vk_monitor.vk_client import VKClient
from modules.publisher.vk_publisher import VKPublisher
from config.runtime import VK_MAIN_TOKENS
from utils.timezone import is_work_hours_for_region

logger = logging.getLogger(__name__)


@app.task(bind=True, name='tasks.real_vk_workflow.collect_and_publish_test')
def collect_and_publish_test(self):
    """
    Реальная задача: собрать посты из тестовой группы и опубликовать дайджест
    
    Тестовая группа: -137760500 (Тест-Инфо)
    """
    logger.info("="*80)
    logger.info("🚀 Starting Real VK Workflow Test")
    logger.info("="*80)
    
    try:
        # Проверка рабочих часов (7:00 - 22:00 MSK)
        # Исключение: "Тест-Инфо" работает круглосуточно
        moscow_tz = pytz.timezone('Europe/Moscow')
        now_moscow = datetime.now(moscow_tz)
        current_hour = now_moscow.hour
        
        work_hours_start = 7
        work_hours_end = 22
        
        # Проверяем рабочие часы для региона "Тест-Инфо"
        region_name = "Тест-Инфо"  # Тестовая группа
        is_work_hours = is_work_hours_for_region(region_name, work_hours_start, work_hours_end)
        
        if not is_work_hours:
            logger.info(f"😴 Outside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)")
            return {
                'success': False,
                'reason': 'outside_work_hours',
                'current_hour': current_hour,
                'work_hours': f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                'timestamp': now_moscow.isoformat()
            }
        
        # Логируем статус работы
        if region_name.lower() in ["тест-инфо", "test-info", "тест инфо"]:
            logger.info(f"🌙 Тест-Инфо работает круглосуточно (время: {current_hour}:00 MSK)")
        else:
            logger.info(f"✅ Inside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)")
        
        # Получаем токен VALSTAN
        valstan_token = VK_MAIN_TOKENS.get("VALSTAN", {}).get("token")
        if not valstan_token:
            logger.error("❌ VALSTAN token not found")
            return {
                'success': False,
                'error': 'VALSTAN token not found',
                'timestamp': now_moscow.isoformat()
            }
        
        logger.info("🔑 Using VALSTAN token for VK API")
        
        # Инициализируем VK клиент
        vk_client = VKClient(valstan_token)
        
        # Тестовая группа
        test_group_id = -137760500
        logger.info(f"📋 Collecting posts from test group: {test_group_id}")
        
        # Собираем посты из группы
        try:
            posts = vk_client.get_wall_posts(test_group_id, count=10)
            logger.info(f"📝 Collected {len(posts)} posts from test group")
            
            if not posts:
                logger.warning("⚠️ No posts found in test group")
                return {
                    'success': True,
                    'posts_collected': 0,
                    'posts_published': 0,
                    'message': 'No posts found in test group',
                    'timestamp': now_moscow.isoformat()
                }
            
            # Показываем информацию о постах
            for i, post in enumerate(posts[:3]):  # Показываем только первые 3
                logger.info(f"📄 Post {i+1}: {post.get('text', 'No text')[:100]}...")
            
            # Создаем дайджест
            digest_text = ""
            
            for i, post in enumerate(posts[:5]):  # Берем первые 5 постов
                text = post.get('text', '')
                if text:
                    # Обрезаем текст до 200 символов
                    short_text = text[:200] + "..." if len(text) > 200 else text
                    digest_text += f"{i+1}. {short_text}\n\n"
            
            logger.info(f"📰 Created digest: {len(digest_text)} characters")
            
            # Публикуем дайджест в тестовую группу
            vk_publisher = VKPublisher(valstan_token)
            
            # Публикуем пост (синхронно)
            import asyncio
            result = asyncio.run(vk_publisher.publish_digest(digest_text, test_group_id))
            
            if result.get('success'):
                logger.info(f"✅ Digest published successfully! Post ID: {result.get('post_id')}")
                return {
                    'success': True,
                    'posts_collected': len(posts),
                    'posts_published': 1,
                    'post_id': result.get('post_id'),
                    'digest_length': len(digest_text),
                    'timestamp': now_moscow.isoformat()
                }
            else:
                logger.error(f"❌ Failed to publish digest: {result.get('error')}")
                return {
                    'success': False,
                    'posts_collected': len(posts),
                    'posts_published': 0,
                    'error': result.get('error'),
                    'timestamp': now_moscow.isoformat()
                }
                
        except Exception as e:
            logger.error(f"❌ Error collecting posts: {e}")
            return {
                'success': False,
                'error': str(e),
                'timestamp': now_moscow.isoformat()
            }
        
    except Exception as e:
        logger.error(f"❌ Real VK workflow failed: {e}", exc_info=True)
        return {
            'success': False,
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }


if __name__ == "__main__":
    # Простой тест
    print("Testing real VK workflow...")
    result = collect_and_publish_test()
    print(f"Result: {result}")
