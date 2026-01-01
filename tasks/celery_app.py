"""
Celery Application

Главное приложение Celery для автоматизации SETKA.

Tasks:
- run_vk_monitoring: Запуск production workflow каждый час
- create_daily_digest: Создание дайджеста за день (18:00)
- cleanup_old_posts: Очистка старых постов (03:00)

Запуск:
    # Worker
    celery -A tasks.celery_app worker --loglevel=info
    
    # Beat scheduler
    celery -A tasks.celery_app beat --loglevel=info
"""
import sys
import os
import logging
from datetime import datetime, timedelta

# Добавляем корневую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from celery import Celery
from celery.schedules import crontab
import asyncio

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='[%(asctime)s] %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Создаем Celery app
app = Celery('setka')
app.config_from_object('config.celery_config')


@app.task(name='tasks.celery_app.run_vk_monitoring')
def run_vk_monitoring():
    """
    Запуск production workflow для мониторинга VK.
    
    Выполняется каждый час в :05 минут.
    Сканирует все активные регионы, применяет фильтры,
    делает AI scoring и создает дайджесты.
    """
    logger.info("=" * 80)
    logger.info("Starting VK monitoring workflow...")
    logger.info("=" * 80)
    
    try:
        from scripts.run_production_workflow import ProductionWorkflow
        
        # Создаем и запускаем workflow
        workflow = ProductionWorkflow()
        result = asyncio.run(workflow.run())
        
        logger.info("VK monitoring completed successfully!")
        logger.info(f"Result: {result}")
        
        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'result': result
        }
        
    except Exception as e:
        logger.error(f"VK monitoring failed: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


@app.task(name='tasks.celery_app.create_daily_digest')
def create_daily_digest():
    """
    Создание дневного дайджеста для всех регионов.
    
    Выполняется каждый день в 18:00.
    Собирает топ-посты за день, создает дайджесты,
    готовит к публикации.
    """
    logger.info("=" * 80)
    logger.info("Creating daily digest...")
    logger.info("=" * 80)
    
    try:
        from database.connection import AsyncSessionLocal
        from database.models import Region, Post
        from modules.aggregation.aggregator import NewsAggregator
        from sqlalchemy import select, and_
        
        async def create_digest():
            async with AsyncSessionLocal() as session:
                # Получаем все активные регионы
                result = await session.execute(
                    select(Region).where(Region.is_active == True)
                )
                regions = list(result.scalars())
                
                aggregator = NewsAggregator(session)
                digests = []
                
                # Создаем дайджест для каждого региона
                for region in regions:
                    logger.info(f"Creating digest for {region.name}...")
                    
                    # Получаем посты за последние 24 часа
                    cutoff_time = datetime.now() - timedelta(hours=24)
                    posts_result = await session.execute(
                        select(Post).where(
                            and_(
                                Post.region_id == region.id,
                                Post.date_published >= cutoff_time,
                                Post.ai_analyzed == True
                            )
                        ).order_by(Post.ai_score.desc()).limit(10)
                    )
                    posts = list(posts_result.scalars())
                    
                    if not posts:
                        logger.warning(f"No posts found for {region.name}")
                        continue
                    
                    # Создаем дайджест
                    digest = await aggregator.create_digest(
                        posts=posts,
                        region=region,
                        max_posts=5
                    )
                    
                    if digest:
                        digests.append({
                            'region': region.name,
                            'posts_count': len(digest.source_posts),
                            'total_views': digest.total_views,
                            'text_length': len(digest.aggregated_text)
                        })
                        logger.info(f"Digest created for {region.name}: {len(digest.source_posts)} posts")
                
                return digests
        
        digests = asyncio.run(create_digest())
        
        logger.info(f"Daily digest completed! Created {len(digests)} digests")
        
        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'digests': digests
        }
        
    except Exception as e:
        logger.error(f"Daily digest failed: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


@app.task(name='tasks.celery_app.check_suggested_posts')
def check_suggested_posts():
    """
    Проверка предложенных постов в главных группах регионов.
    
    Выполняется каждый час с 8:00 до 22:00.
    Проверяет все главные группы регионов (с префиксом ИНФО) на наличие
    предложенных постов от посетителей.
    
    Результаты сохраняются в Redis и отправляются в Telegram.
    """
    logger.info("=" * 80)
    logger.info("Checking suggested posts in region groups...")
    logger.info("=" * 80)
    
    try:
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.vk_suggested_checker import VKSuggestedChecker
        from modules.notifications.storage import NotificationsStorage
        from config.runtime import VK_TOKENS, TELEGRAM_TOKENS
        from sqlalchemy import select
        import requests
        
        async def check():
            # Получаем все регионы с главными группами
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Region).where(
                        Region.vk_group_id.isnot(None),
                        Region.is_active == True
                    )
                )
                regions = list(result.scalars())
                
                if not regions:
                    logger.warning("No regions with VK groups found")
                    return []
                
                logger.info(f"Checking {len(regions)} region groups...")
                
                # Подготавливаем данные для проверки
                region_groups = [
                    {
                        'region_id': r.id,
                        'region_name': r.name,
                        'region_code': r.code,
                        'vk_group_id': r.vk_group_id
                    }
                    for r in regions
                ]
                
                # Проверяем предложенные посты
                vk_token = VK_TOKENS.get("VALSTAN")
                if not vk_token:
                    logger.error("VK token not found")
                    return []
                
                checker = VKSuggestedChecker(vk_token)
                notifications = await checker.check_all_region_groups(region_groups)
                
                # Сохраняем в Redis
                storage = NotificationsStorage()
                storage.save_notifications(notifications)
                
                logger.info(f"Found {len(notifications)} groups with suggested posts")
                
                # Отправляем в Telegram если есть уведомления
                if notifications:
                    telegram_token = TELEGRAM_TOKENS.get("AFONYA")
                    telegram_chat_id = "-4512545012"  # ID чата для уведомлений
                    
                    if telegram_token:
                        message = "📬 *Предложенные посты в группах:*\n\n"
                        
                        for notif in notifications:
                            message += f"📍 *{notif['region_name']}*\n"
                            message += f"   Постов: {notif['suggested_count']}\n"
                            message += f"   🔗 [Проверить]({notif['url']})\n\n"
                        
                        try:
                            requests.post(
                                f"https://api.telegram.org/bot{telegram_token}/sendMessage",
                                json={
                                    'chat_id': telegram_chat_id,
                                    'text': message,
                                    'parse_mode': 'Markdown',
                                    'disable_web_page_preview': True
                                },
                                timeout=10
                            )
                            logger.info("Telegram notification sent")
                        except Exception as e:
                            logger.error(f"Failed to send Telegram notification: {e}")
                
                return notifications
        
        notifications = asyncio.run(check())
        
        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'notifications_count': len(notifications),
            'notifications': notifications
        }
        
    except Exception as e:
        logger.error(f"Failed to check suggested posts: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


@app.task(name='tasks.celery_app.check_unread_messages')
def check_unread_messages():
    """
    Проверка непрочитанных сообщений (VK community messages) в главных группах регионов.

    Выполняется каждый час с 8:00 до 22:00 (MSK).
    Результаты сохраняются в Redis под ключом setka:notifications:unread_messages.
    """
    from datetime import datetime
    import pytz

    moscow_tz = pytz.timezone('Europe/Moscow')
    now_moscow = datetime.now(moscow_tz)
    current_hour = now_moscow.hour

    WORK_HOURS_START = 8
    WORK_HOURS_END = 22

    if not (WORK_HOURS_START <= current_hour < WORK_HOURS_END):
        logger.info(
            f"😴 Outside work hours (current: {current_hour}:00 MSK, "
            f"work: {WORK_HOURS_START}:00-{WORK_HOURS_END}:00)"
        )
        logger.info("⏸️  Skipping VK unread messages check (server resting)")
        return {
            'skipped': True,
            'reason': f'Outside work hours ({current_hour}:00 MSK)',
            'work_hours': f'{WORK_HOURS_START}:00-{WORK_HOURS_END}:00 MSK',
            'next_check': f'Next check at {WORK_HOURS_START}:00 MSK'
        }

    logger.info("=" * 80)
    logger.info("Checking unread messages in region groups...")
    logger.info("=" * 80)

    try:
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.vk_messages_checker import VKMessagesChecker
        from modules.notifications.storage import NotificationsStorage
        from config.runtime import VK_TOKENS
        from sqlalchemy import select

        async def check():
            # Получаем все регионы с главными группами
            async with AsyncSessionLocal() as session:
                result = await session.execute(
                    select(Region).where(
                        Region.vk_group_id.isnot(None),
                        Region.is_active == True
                    )
                )
                regions = list(result.scalars())

                if not regions:
                    logger.warning("No regions with VK groups found")
                    return []

                logger.info(f"Checking {len(regions)} region groups for unread messages...")

                region_groups = [
                    {
                        'region_id': r.id,
                        'region_name': r.name,
                        'region_code': r.code,
                        'vk_group_id': r.vk_group_id
                    }
                    for r in regions
                ]

                vk_token = VK_TOKENS.get("VALSTAN")
                if not vk_token:
                    logger.error("VK token not found")
                    return []

                checker = VKMessagesChecker(vk_token)
                notifications = await checker.check_all_region_groups(region_groups)

                storage = NotificationsStorage()
                storage.save_notifications(notifications, 'unread_messages')

                logger.info(f"Found {len(notifications)} groups with unread messages")
                return notifications

        notifications = asyncio.run(check())

        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'notifications_count': len(notifications),
            'notifications': notifications
        }

    except Exception as e:
        logger.error(f"Failed to check unread messages: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


@app.task(name='tasks.celery_app.check_recent_comments')
def check_recent_comments():
    """
    Проверка комментариев за последние 24 часа ТОЛЬКО в главных региональных сообществах (ИНФО).

    Источник групп: таблица regions (поле vk_group_id, названия с " - ИНФО").
    Результаты сохраняются в Redis под ключом setka:notifications:recent_comments.
    """
    from datetime import datetime, timedelta
    import pytz

    moscow_tz = pytz.timezone('Europe/Moscow')
    now_moscow = datetime.now(moscow_tz)
    current_hour = now_moscow.hour

    WORK_HOURS_START = 8
    WORK_HOURS_END = 22

    if not (WORK_HOURS_START <= current_hour < WORK_HOURS_END):
        logger.info(
            f"😴 Outside work hours (current: {current_hour}:00 MSK, "
            f"work: {WORK_HOURS_START}:00-{WORK_HOURS_END}:00)"
        )
        logger.info("⏸️  Skipping VK recent comments check (server resting)")
        return {
            'skipped': True,
            'reason': f'Outside work hours ({current_hour}:00 MSK)',
            'work_hours': f'{WORK_HOURS_START}:00-{WORK_HOURS_END}:00 MSK',
            'next_check': f'Next check at {WORK_HOURS_START}:00 MSK'
        }

    logger.info("=" * 80)
    logger.info("Checking recent comments (last 24h) under posts of all communities...")
    logger.info("=" * 80)

    try:
        from database.connection import AsyncSessionLocal
        from database.models import Region
        from modules.notifications.vk_comments_checker import VKCommentsChecker
        from modules.notifications.storage import NotificationsStorage
        from config.runtime import VK_TOKENS
        from sqlalchemy import select

        cutoff_dt = datetime.utcnow() - timedelta(hours=24)
        cutoff_ts = int(cutoff_dt.timestamp())

        async def check():
            vk_token = VK_TOKENS.get("VALSTAN")
            if not vk_token:
                logger.error("VK token not found")
                return []

            async with AsyncSessionLocal() as session:
                # Берём только главные ИНФО-группы регионов
                rows = await session.execute(
                    select(Region.id, Region.code, Region.name, Region.vk_group_id)
                    .where(
                        Region.is_active == True,
                        Region.vk_group_id.isnot(None),
                    )
                    .order_by(Region.id)
                )
                regions = rows.all()

                region_groups = []
                for region_id, region_code, region_name, vk_group_id in regions:
                    # Дополнительный фильтр по префиксу/маркеру ИНФО
                    if "ИНФО" not in (region_name or ""):
                        continue
                    region_groups.append({
                        "region_id": region_id,
                        "region_code": region_code,
                        "region_name": region_name,
                        "vk_group_id": vk_group_id
                    })

                checker = VKCommentsChecker(vk_token)
                notifications = await checker.check_recent_comments_for_region_groups(
                    region_groups=region_groups,
                    cutoff_ts=cutoff_ts
                )

                storage = NotificationsStorage()
                storage.save_notifications(notifications, 'recent_comments')

                logger.info(f"Found {len(notifications)} recent comments (main INFO groups only)")
                return notifications

        notifications = asyncio.run(check())

        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'comments_count': len(notifications),
            'notifications': notifications
        }

    except Exception as e:
        logger.error(f"Failed to check recent comments: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


@app.task(name='tasks.celery_app.cleanup_old_posts')
def cleanup_old_posts():
    """
    Очистка старых постов из БД.
    
    Выполняется каждый день в 03:00.
    Удаляет посты старше 30 дней для освобождения места.
    """
    logger.info("=" * 80)
    logger.info("Cleaning up old posts...")
    logger.info("=" * 80)
    
    try:
        from database.connection import AsyncSessionLocal
        from database.models import Post
        from sqlalchemy import delete
        
        async def cleanup():
            async with AsyncSessionLocal() as session:
                # Удаляем посты старше 30 дней
                cutoff_date = datetime.now() - timedelta(days=30)
                
                result = await session.execute(
                    delete(Post).where(Post.date_published < cutoff_date)
                )
                
                deleted_count = result.rowcount
                await session.commit()
                
                return deleted_count
        
        deleted_count = asyncio.run(cleanup())
        
        logger.info(f"Cleanup completed! Deleted {deleted_count} old posts")
        
        return {
            'success': True,
            'timestamp': datetime.now().isoformat(),
            'deleted_count': deleted_count
        }
        
    except Exception as e:
        logger.error(f"Cleanup failed: {e}", exc_info=True)
        return {
            'success': False,
            'timestamp': datetime.now().isoformat(),
            'error': str(e)
        }


# Расписания (Beat Schedule)
app.conf.beat_schedule = {
    # VK мониторинг каждый час в X:05
    'monitoring-hourly': {
        'task': 'tasks.celery_app.run_vk_monitoring',
        'schedule': crontab(minute=5),  # Каждый час на 5-й минуте
        'options': {
            'expires': 3000,  # Task expires after 50 minutes
        }
    },
    
    # Проверка предложенных постов каждый час с 8:00 до 22:00 в X:15
    'check-suggested-hourly': {
        'task': 'tasks.celery_app.check_suggested_posts',
        'schedule': crontab(minute=15, hour='8-22'),  # Каждый час 8-22 на 15-й минуте
        'options': {
            'expires': 3000,
        }
    },

    # Проверка непрочитанных сообщений каждый час с 8:00 до 22:00 в X:16
    'check-unread-messages-hourly': {
        'task': 'tasks.celery_app.check_unread_messages',
        'schedule': crontab(minute=16, hour='8-22'),  # Каждый час 8-22 на 16-й минуте
        'options': {
            'expires': 3000,
        }
    },

    # Проверка комментариев за сутки каждый час с 8:00 до 22:00 в X:17
    'check-recent-comments-hourly': {
        'task': 'tasks.celery_app.check_recent_comments',
        'schedule': crontab(minute=17, hour='8-22'),  # Каждый час 8-22 на 17-й минуте
        'options': {
            'expires': 3000,
        }
    },
    
    # Дневной дайджест в 18:00
    'digest-daily': {
        'task': 'tasks.celery_app.create_daily_digest',
        'schedule': crontab(hour=18, minute=0),  # 18:00 каждый день
        'options': {
            'expires': 3000,
        }
    },
    
    # Очистка старых постов в 03:00
    'cleanup-daily': {
        'task': 'tasks.celery_app.cleanup_old_posts',
        'schedule': crontab(hour=3, minute=0),  # 03:00 каждый день
        'options': {
            'expires': 3000,
        }
    },
}


if __name__ == '__main__':
    # Для отладки
    logger.info("Celery app configured successfully!")
    logger.info(f"Broker: {app.conf.broker_url}")
    logger.info(f"Backend: {app.conf.result_backend}")
    logger.info(f"Timezone: {app.conf.timezone}")
    logger.info(f"Beat schedule: {list(app.conf.beat_schedule.keys())}")

