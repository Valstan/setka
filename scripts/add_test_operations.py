#!/usr/bin/env python3
"""
Скрипт для добавления тестовых операций в систему мониторинга
"""
import asyncio
import logging
from datetime import datetime, timedelta

from modules.operation_tracking import operation_tracker

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def add_test_operations():
    """Добавить тестовые операции для демонстрации"""
    logger.info("🚀 Добавляем тестовые операции...")

    # Очищаем существующие операции
    operation_tracker.clear_operations()

    # Добавляем несколько завершенных операций
    test_operations = [
        {
            "id": "monitoring_mi_1",
            "type": "monitoring",
            "description": "Мониторинг региона Малмыж",
            "region": "mi",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=15),
            "end_time": datetime.utcnow() - timedelta(minutes=14),
            "details": {"communities_count": 97, "posts_found": 5},
        },
        {
            "id": "filtering_mi_1",
            "type": "filtering",
            "description": "Фильтрация постов региона Малмыж",
            "region": "mi",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=14),
            "end_time": datetime.utcnow() - timedelta(minutes=13),
            "details": {"posts_count": 5, "accepted": 3, "rejected": 2},
        },
        {
            "id": "publishing_mi_1",
            "type": "publishing",
            "description": "Публикация сводки региона Малмыж",
            "region": "mi",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=13),
            "end_time": datetime.utcnow() - timedelta(minutes=12),
            "details": {"posts_published": 3, "vk_post_id": "123456789"},
        },
        {
            "id": "monitoring_nolinsk_1",
            "type": "monitoring",
            "description": "Мониторинг региона Нолинск",
            "region": "nolinsk",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=10),
            "end_time": datetime.utcnow() - timedelta(minutes=9),
            "details": {"communities_count": 62, "posts_found": 8},
        },
        {
            "id": "filtering_nolinsk_1",
            "type": "filtering",
            "description": "Фильтрация постов региона Нолинск",
            "region": "nolinsk",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=9),
            "end_time": datetime.utcnow() - timedelta(minutes=8),
            "details": {"posts_count": 8, "accepted": 5, "rejected": 3},
        },
        {
            "id": "publishing_nolinsk_1",
            "type": "publishing",
            "description": "Публикация сводки региона Нолинск",
            "region": "nolinsk",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=8),
            "end_time": datetime.utcnow() - timedelta(minutes=7),
            "details": {"posts_published": 5, "vk_post_id": "987654321"},
        },
        {
            "id": "monitoring_arbazh_1",
            "type": "monitoring",
            "description": "Мониторинг региона Арбаж",
            "region": "arbazh",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=5),
            "end_time": datetime.utcnow() - timedelta(minutes=4),
            "details": {"communities_count": 61, "posts_found": 2},
        },
        {
            "id": "filtering_arbazh_1",
            "type": "filtering",
            "description": "Фильтрация постов региона Арбаж",
            "region": "arbazh",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=4),
            "end_time": datetime.utcnow() - timedelta(minutes=3),
            "details": {"posts_count": 2, "accepted": 1, "rejected": 1},
        },
        {
            "id": "publishing_arbazh_1",
            "type": "publishing",
            "description": "Публикация сводки региона Арбаж",
            "region": "arbazh",
            "status": "completed",
            "start_time": datetime.utcnow() - timedelta(minutes=3),
            "end_time": datetime.utcnow() - timedelta(minutes=2),
            "details": {"posts_published": 1, "vk_post_id": "456789123"},
        },
        {
            "id": "monitoring_bal_1",
            "type": "monitoring",
            "description": "Мониторинг региона Балтаси",
            "region": "bal",
            "status": "error",
            "start_time": datetime.utcnow() - timedelta(minutes=1),
            "end_time": datetime.utcnow() - timedelta(seconds=30),
            "details": {"error": "Timeout при подключении к VK API"},
        },
    ]

    # Добавляем операции в трекер
    for op in test_operations:
        operation_tracker.operations[op["id"]] = op
        logger.info(f"✅ Добавлена операция: {op['description']}")

    # Добавляем одну активную операцию
    operation_tracker.start_operation(
        "monitoring_kukmor_1",
        "monitoring",
        "Мониторинг региона Кукмор",
        "kukmor",
        {"communities_count": 44, "progress": 25},
    )

    logger.info("🎉 Тестовые операции добавлены!")
    logger.info(f"Всего операций: {len(operation_tracker.operations)}")
    logger.info(f"Активных операций: {len(operation_tracker.get_active_operations())}")


async def main():
    await add_test_operations()


if __name__ == "__main__":
    asyncio.run(main())
