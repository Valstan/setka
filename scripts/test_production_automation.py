#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тестовый скрипт для проверки Production Automation

Тестирует:
1. Запуск workflow для региона mi
2. Создание дайджеста
3. Публикацию в тестовую группу VK
4. Telegram уведомления
"""
import asyncio
import sys
import os
import logging
from datetime import datetime

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/home/valstan/SETKA/logs/test_automation.log')
    ]
)

logger = logging.getLogger(__name__)


async def test_production_workflow():
    """Тест production workflow для одного региона"""
    logger.info("🧪 Testing Production Workflow")
    logger.info("="*50)
    
    try:
        # Импорты
        from scripts.run_production_workflow import ProductionWorkflow
        from modules.notifications.telegram_notifier import get_telegram_notifier
        
        # Создать workflow
        workflow = ProductionWorkflow()
        
        # Тест 1: Запуск workflow для региона mi
        logger.info("📊 Test 1: Running workflow for region 'mi'")
        result = await workflow.run_single_region(
            region_code='mi',
            max_posts=10,  # Мало постов для быстрого теста
            publish_mode='test'
        )
        
        logger.info(f"✅ Workflow result: {result}")
        
        # Проверки
        assert result['success'], f"Workflow failed: {result.get('error')}"
        assert result['region_code'] == 'mi', f"Wrong region: {result['region_code']}"
        assert 'posts_collected' in result, "Missing posts_collected"
        assert 'posts_accepted' in result, "Missing posts_accepted"
        assert 'posts_published' in result, "Missing posts_published"
        
        logger.info(f"✅ Test 1 passed: {result['posts_collected']} collected, {result['posts_accepted']} accepted, {result['posts_published']} published")
        
        # Тест 2: Telegram уведомления
        logger.info("\n📱 Test 2: Testing Telegram notifications")
        notifier = get_telegram_notifier()
        
        if notifier:
            # Тест соединения
            connection_ok = await notifier.test_connection()
            assert connection_ok, "Telegram connection test failed"
            logger.info("✅ Telegram connection test passed")
            
            # Тест уведомления об ошибке (симуляция)
            test_error = "Test error for automation testing"
            test_context = {
                'region_code': 'mi',
                'task_name': 'test_automation',
                'posts_count': result['posts_collected']
            }
            
            notification_sent = await notifier.send_error_notification(test_error, test_context)
            assert notification_sent, "Failed to send test notification"
            logger.info("✅ Test notification sent successfully")
        else:
            logger.warning("⚠️ TelegramNotifier not configured, skipping notification tests")
        
        # Тест 3: Проверка конфигурации
        logger.info("\n⚙️ Test 3: Checking configuration")
        from config.runtime import (
            VK_TEST_GROUP_ID, 
            VK_PRODUCTION_GROUPS, 
            PRODUCTION_WORKFLOW_CONFIG
        )
        
        assert VK_TEST_GROUP_ID is not None, "VK_TEST_GROUP_ID not configured"
        assert isinstance(VK_PRODUCTION_GROUPS, dict), "VK_PRODUCTION_GROUPS not configured"
        assert 'mi' in VK_PRODUCTION_GROUPS, "Region 'mi' not in VK_PRODUCTION_GROUPS"
        assert PRODUCTION_WORKFLOW_CONFIG['publish_mode'] == 'test', "Publish mode should be 'test'"
        
        logger.info("✅ Configuration test passed")
        
        # Итоговый результат
        logger.info("\n" + "="*50)
        logger.info("🎉 ALL TESTS PASSED!")
        logger.info("="*50)
        logger.info(f"Workflow duration: {result['duration']:.1f}s")
        logger.info(f"Posts collected: {result['posts_collected']}")
        logger.info(f"Posts accepted: {result['posts_accepted']}")
        logger.info(f"Posts published: {result['posts_published']}")
        logger.info(f"Publish mode: {result['publish_mode']}")
        
        if result['errors']:
            logger.warning(f"Errors encountered: {result['errors']}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Test failed: {e}", exc_info=True)
        return False


async def test_celery_task():
    """Тест Celery task напрямую"""
    logger.info("\n🔧 Testing Celery task directly")
    
    try:
        from tasks.production_workflow_tasks import run_single_region_workflow
        
        result = await run_single_region_workflow(
            region_code='mi',
            max_posts=5,
            publish_mode='test'
        )
        
        logger.info(f"✅ Celery task result: {result}")
        return result.get('success', False)
        
    except Exception as e:
        logger.error(f"❌ Celery task test failed: {e}", exc_info=True)
        return False


async def test_vk_publisher():
    """Тест VK Publisher"""
    logger.info("\n📤 Testing VK Publisher")
    
    try:
        from modules.publisher.vk_publisher import VKPublisher
        from config.runtime import VK_TOKENS, VK_TEST_GROUP_ID
        
        publisher = VKPublisher(VK_TOKENS["VALSTAN"])
        
        # Тест получения целевой группы
        test_group = publisher.get_target_group_id('mi', 'test')
        assert test_group == VK_TEST_GROUP_ID, f"Wrong test group: {test_group}"
        
        production_group = publisher.get_target_group_id('mi', 'production')
        assert production_group == VK_TEST_GROUP_ID, f"Wrong production group: {production_group}"
        
        # Тест получения информации о группе
        group_info = publisher.get_group_info(VK_TEST_GROUP_ID)
        if group_info:
            logger.info(f"✅ Group info: {group_info['name']} ({group_info['url']})")
        else:
            logger.warning("⚠️ Could not get group info (may be access issue)")
        
        logger.info("✅ VK Publisher test passed")
        return True
        
    except Exception as e:
        logger.error(f"❌ VK Publisher test failed: {e}", exc_info=True)
        return False


async def main():
    """Главная функция тестирования"""
    logger.info("🚀 Starting Production Automation Tests")
    logger.info(f"⏰ Test started at: {datetime.now()}")
    
    tests_passed = 0
    total_tests = 0
    
    # Тест 1: Production Workflow
    total_tests += 1
    if await test_production_workflow():
        tests_passed += 1
    
    # Тест 2: Celery Task
    total_tests += 1
    if await test_celery_task():
        tests_passed += 1
    
    # Тест 3: VK Publisher
    total_tests += 1
    if await test_vk_publisher():
        tests_passed += 1
    
    # Итоговый результат
    logger.info("\n" + "="*60)
    logger.info("📊 TEST SUMMARY")
    logger.info("="*60)
    logger.info(f"Tests passed: {tests_passed}/{total_tests}")
    
    if tests_passed == total_tests:
        logger.info("🎉 ALL TESTS PASSED! Production automation is ready!")
        return 0
    else:
        logger.error(f"❌ {total_tests - tests_passed} tests failed!")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
