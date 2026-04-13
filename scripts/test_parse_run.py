#!/usr/bin/env python3
"""
Тестовый запуск парсинга-фильтрации-постинга для региона 'test'
Запускает ОДИН прогон с детальным логированием
"""
import asyncio
import logging
import sys
from datetime import datetime
from typing import List, Dict, Any

# Настройка логирования
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('/home/valstan/SETKA/logs/test_parse_run.log', mode='w')
    ]
)
logger = logging.getLogger('test_parse_run')

# Отключаем шумные логи
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('celery').setLevel(logging.WARNING)


async def main():
    logger.info('='*80)
    logger.info('ТЕСТОВЫЙ ЗАПУСК ПАРСИНГА-ФИЛЬТРАЦИИ-ПОСТИНГА')
    logger.info(f'Время запуска: {datetime.now()}')
    logger.info('='*80)

    # Импортируем после настройки логов
    from database.connection import AsyncSessionLocal
    from database.models import Region, Community
    from database.models_extended import RegionConfig, WorkTable
    from modules.vk_monitor.advanced_parser import AdvancedVKParser
    from modules.vk_monitor.vk_client import VKClient
    from modules.publisher.digest_builder import DigestBuilder
    from modules.publisher.vk_publisher_extended import VKPublisher
    from modules.region_config import RegionConfigManager

    async with AsyncSessionLocal() as session:
        # 1. Проверяем регион
        logger.info('\n[ШАГ 1] Проверка региона и настроек')
        from sqlalchemy import select
        result = await session.execute(
            select(Region).where(Region.code == 'test')
        )
        region = result.scalars().first()
        if not region:
            logger.error('❌ Регион test не найден в БД!')
            return {'success': False, 'error': 'Region not found'}
        logger.info(f'✅ Регион найден: {region.name} (vk_group_id={region.vk_group_id})')

        # 2. Проверяем сообщества
        logger.info('\n[ШАГ 2] Проверка сообществ для парсинга')
        result = await session.execute(
            select(Community).where(
                Community.region_id == region.id,
                Community.is_active == True
            ).order_by(Community.category)
        )
        communities = result.scalars().all()
        logger.info(f'Найдено {len(communities)} сообществ:')
        for c in communities:
            logger.info(f'  - {c.name:40s} | category={c.category:20s} | vk_id={c.vk_id}')

        if not communities:
            logger.warning('⚠️ Нет активных сообществ для парсинга!')
            return {'success': False, 'error': 'No communities found'}

        # 3. Инициализируем VK клиента и парсер
        logger.info('\n[ШАГ 3] Инициализация VK клиента и парсера')

        from config.runtime import VK_TOKENS, get_publish_token, VK_PUBLISH_TOKEN_NAME
        if not VK_TOKENS:
            logger.error('❌ VK токены не настроены!')
            return {'success': False, 'error': 'No VK tokens configured'}

        # Для парсинга — первый доступный токен
        parse_token_name, parse_token_value = next(iter(VK_TOKENS.items()))
        logger.info(f'Токен для парсинга: {parse_token_name}')

        vk_client = VKClient(parse_token_value)
        logger.info('✅ VK клиент инициализирован')

        # Для публикации — ТОЛЬКО designated publish token
        publish_token = get_publish_token()
        if not publish_token:
            logger.error('❌ Нет токена для публикации!')
            return {'success': False, 'error': 'No publish token configured'}
        
        # Находим имя publish-токена для логирования
        publish_token_name = VK_PUBLISH_TOKEN_NAME or "UNKNOWN"
        for name, tok in VK_TOKENS.items():
            if tok == publish_token:
                publish_token_name = name
                break
        logger.info(f'Токен для публикации: {publish_token_name} (publish only)')

        parser = AdvancedVKParser(vk_client)

        # 4. Загружаем work table для отслеживания опубликованного
        logger.info('\n[ШАГ 4] Загрузка work table (дубликаты)')
        result = await session.execute(
            select(WorkTable).where(
                WorkTable.region_code == 'test',
                WorkTable.theme == 'novost'
            )
        )
        work_table = result.scalars().first()
        if work_table:
            logger.info(f'Work table найден: {len(work_table.lip or [])} LIP записей, {len(work_table.hash or [])} hash записей')
        else:
            logger.info('Work table не найден, создаём новый')
            work_table = WorkTable(region_code='test', theme='novost', lip=[], hash=[])
            session.add(work_table)
            await session.commit()

        # 5. Берём сообщества категории 'news' или первые 5 для теста
        test_communities = communities[:5]  # Ограничим 5 для теста
        logger.info(f'\n[ШАГ 5] Парсинг {len(test_communities)} сообществ')

        # Собираем vk_id
        community_vk_ids = [c.vk_id for c in test_communities]
        logger.info(f'VK IDs: {community_vk_ids}')

        # 6. Парсим посты
        logger.info('\n[ШАГ 6] Запуск парсинга')
        try:
            posts = await parser.parse_posts_from_communities(
                community_ids=community_vk_ids,
                theme='novost',
                region_config=None,  # Будем использовать базовый парсинг
                work_table_lip=work_table.lip or [],
                work_table_hash=work_table.hash or [],
                recent_text_fingerprints=[],
                count_per_community=10  # Ограничим для теста
            )
            logger.info(f'✅ Спарсено {len(posts)} постов после фильтрации')
        except Exception as e:
            logger.error(f'❌ Ошибка парсинга: {e}', exc_info=True)
            return {'success': False, 'error': f'Parse error: {e}'}

        # 7. Выводим статистику парсера
        logger.info('\n[ШАГ 7] Статистика парсера')
        stats = parser.get_stats()
        for key, value in stats.items():
            logger.info(f'  {key}: {value}')

        if not posts:
            logger.info('\n⚠️ Нет постов для публикации (возможно все отфильтрованы)')
            return {'success': True, 'posts_count': 0, 'message': 'No posts after filtering'}

        # 8. Разделяем посты по тональности и строим дайджесты
        logger.info('\n[ШАГ 8] Разделение постов по тональности')
        from modules.publisher.digest_splitter import DigestSplitter

        splitter = DigestSplitter()
        split_result = splitter.split_with_stats(posts)
        mourning_posts = split_result['mourning_posts']
        regular_posts = split_result['regular_posts']
        
        logger.info(f'Mourning постов: {len(mourning_posts)}')
        logger.info(f'Regular постов: {len(regular_posts)}')
        if split_result['mourning_markers']:
            logger.info(f'Найдены mourning-маркеры:')
            for m in split_result['mourning_markers']:
                logger.info(f"  LIP={m['lip']}, markers={m['mourning_count']}")

        # 8a. Строим обычный дайджест
        logger.info('\n[ШАГ 8a] Построение обычного дайджеста')
        builder = DigestBuilder(
            header='📰 Тестовый запуск',
            hashtags=['#тест'],
            local_hashtag='#тест',
            max_text_length=4096,
        )
        digest_result = builder.build_digest(regular_posts)
        logger.info(f'Обычный digest: {digest_result.post_count} постов')
        logger.info(f'Text length: {len(digest_result.text)}')
        logger.info(f'Attachments: {len(digest_result.attachments_list)}')

        # 8b. Строим mourning дайджест (если есть)
        mourning_digest_result = None
        if mourning_posts:
            logger.info('\n[ШАГ 8b] Построение mourning дайджеста')
            mourning_builder = DigestBuilder(
                header='🕯 Скорбим',
                hashtags=[],
                local_hashtag='',
                max_text_length=4096,
            )
            mourning_digest_result = mourning_builder.build_digest(mourning_posts)
            logger.info(f'Mourning digest: {mourning_digest_result.post_count} постов')
            logger.info(f'Text length: {len(mourning_digest_result.text)}')

        # 9. Публикуем в тестовую группу (VKPublisher создаёт свой клиент с publish токеном)
        logger.info('\n[ШАГ 9] Публикация')
        vk_publisher = VKPublisher(test_polygon_mode=True)
        
        publish_results = {}

        # 9a. Публикуем обычный дайджест
        logger.info(f'[ШАГ 9a] Публикация обычного дайджеста')
        try:
            publish_result = await vk_publisher.publish_digest(
                group_id=region.vk_group_id,
                text=digest_result.text,
                attachments=digest_result.attachments_list,
            )
            logger.info(f'Результат публикации: {publish_result}')
            publish_results['regular'] = publish_result
        except Exception as e:
            logger.error(f'❌ Ошибка публикации обычного дайджеста: {e}', exc_info=True)
            publish_results['regular'] = {'success': False, 'error': str(e)}

        # 9b. Публикуем mourning дайджест (если есть)
        if mourning_digest_result and mourning_digest_result.post_count > 0:
            logger.info(f'[ШАГ 9b] Публикация mourning дайджеста')
            try:
                mourning_publish = await vk_publisher.publish_digest(
                    group_id=region.vk_group_id,
                    text=mourning_digest_result.text,
                    attachments=mourning_digest_result.attachments_list,
                )
                logger.info(f'Результат публикации mourning: {mourning_publish}')
                publish_results['mourning'] = mourning_publish
            except Exception as e:
                logger.error(f'❌ Ошибка публикации mourning дайджеста: {e}', exc_info=True)
                publish_results['mourning'] = {'success': False, 'error': str(e)}

        # 10. Обновляем work table (все опубликованные LIP)
        logger.info('\n[ШАГ 10] Обновление work table')
        existing_lip = work_table.lip or []
        existing_lip.extend(digest_result.posts_included)
        if mourning_digest_result and mourning_digest_result.posts_included:
            existing_lip.extend(mourning_digest_result.posts_included)
        if len(existing_lip) > 30:
            existing_lip = existing_lip[-30:]
        work_table.lip = existing_lip
        await session.commit()
        logger.info(f'Work table обновлён: {len(existing_lip)} LIP записей')

        # Итог
        logger.info('\n' + '='*80)
        logger.info('✅ ТЕСТОВЫЙ ЗАПУСК ЗАВЕРШЁН УСПЕШНО')
        logger.info(f'Время завершения: {datetime.now()}')
        logger.info(f'Постов с парсено: {len(posts)}')
        logger.info(f'  Mourning: {len(mourning_posts)}')
        logger.info(f'  Regular: {len(regular_posts)}')
        logger.info(f'Обычный digest: {digest_result.post_count} постов (URL: {publish_results.get("regular", {}).get("url", "N/A")})')
        if mourning_digest_result:
            logger.info(f'Mourning digest: {mourning_digest_result.post_count} постов (URL: {publish_results.get("mourning", {}).get("url", "N/A")})')
        logger.info(f'Публикация токеном: {publish_token_name}')
        logger.info('='*80)

        return {
            'success': True,
            'posts_parsed': len(posts),
            'mourning_posts': len(mourning_posts),
            'regular_posts': len(regular_posts),
            'digest_result': {
                'post_count': digest_result.post_count,
                'text_length': len(digest_result.text),
                'publish': publish_results.get('regular'),
            },
            'mourning_result': {
                'post_count': mourning_digest_result.post_count if mourning_digest_result else 0,
                'text_length': len(mourning_digest_result.text) if mourning_digest_result else 0,
                'publish': publish_results.get('mourning'),
            } if mourning_digest_result else None,
            'publish_token': publish_token_name,
            'stats': stats,
        }


if __name__ == '__main__':
    try:
        result = asyncio.run(main())
        print('\n\n===== ИТОГ =====')
        print(f'Result: {result}')
    except Exception as e:
        logger.error(f'КРИТИЧЕСКАЯ ОШИБКА: {e}', exc_info=True)
        print(f'\n\n===== ИТОГ =====')
        print(f'ERROR: {e}')
