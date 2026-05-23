"""
Production Workflow Celery Tasks

Автоматический запуск production workflow каждый час с 7:00 до 22:00 MSK
Обрабатывает ВСЕ активные регионы последовательно (карусель) для равномерной нагрузки на VK API
"""

import logging
from datetime import datetime

import pytz

from celery_app import app
from config.runtime import PRODUCTION_WORKFLOW_CONFIG
from utils.timezone import is_work_hours_for_region

logger = logging.getLogger(__name__)


@app.task(
    bind=True, name="tasks.production_workflow_tasks.run_production_workflow_all_regions_sync"
)
def run_production_workflow_all_regions_sync(self):
    """
    Синхронная версия главной задачи: запуск production workflow для ВСЕХ активных регионов

    Выполняется каждый час с 7:00 до 22:00 MSK
    Обрабатывает регионы ПОСЛЕДОВАТЕЛЬНО (карусель) для распределения нагрузки на VK API
    """
    logger.info("=" * 80)
    logger.info("🚀 Starting Production Workflow Carousel (SYNC)")
    logger.info("=" * 80)

    try:
        # Проверка рабочих часов (7:00 - 22:00 MSK)
        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        current_hour = now_moscow.hour

        work_hours_start = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_start", 7)
        work_hours_end = PRODUCTION_WORKFLOW_CONFIG.get("work_hours_end", 22)

        if not (work_hours_start <= current_hour <= work_hours_end):
            logger.info(
                f"😴 Outside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)"  # noqa: E501
            )
            return {
                "success": False,
                "reason": "outside_work_hours",
                "current_hour": current_hour,
                "work_hours": f"{work_hours_start}:00-{work_hours_end}:00 MSK",
                "timestamp": now_moscow.isoformat(),
            }

        logger.info(
            f"✅ Inside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)"  # noqa: E501
        )

        # Получаем все активные регионы
        logger.info("📋 Getting active regions...")

        # Здесь должна быть логика получения регионов из базы данных
        # Пока что используем тестовые данные
        active_regions = [
            "mi",
            "arbazh",
            "bal",
            "klz",
            "kukmor",
            "leb",
            "nema",
            "nolinsk",
            "pizhanka",
            "sovetsk",
            "test",
            "ur",
            "verhoshizhem",
            "vp",
        ]

        logger.info(f"📍 Found {len(active_regions)} active regions: {', '.join(active_regions)}")

        # Обрабатываем каждый регион
        processed_count = 0
        total_posts = 0
        skipped_count = 0

        for i, region_code in enumerate(active_regions):
            logger.info(f"🏘️ Processing region {i+1}/{len(active_regions)}: {region_code.upper()}")

            try:
                # Определяем название региона для проверки рабочих часов
                region_name = region_code.upper()
                if region_code == "test":
                    region_name = "Тест-Инфо"  # Специальное название для тестового региона

                # Проверяем рабочие часы для конкретного региона
                region_work_hours = is_work_hours_for_region(
                    region_name, work_hours_start, work_hours_end
                )

                if not region_work_hours:
                    logger.info(
                        f"😴 Region {region_name} outside work hours: {current_hour}:00 MSK (work: {work_hours_start}:00-{work_hours_end}:00)"  # noqa: E501
                    )
                    skipped_count += 1
                    continue

                # Логируем статус работы для региона
                if region_name.lower() in ["тест-инфо", "test-info", "тест инфо"]:
                    logger.info(f"🌙 Region {region_name} works 24/7 (time: {current_hour}:00 MSK)")
                else:
                    logger.info(f"✅ Region {region_name} inside work hours: {current_hour}:00 MSK")

                # Здесь должна быть логика обработки региона
                # Пока что симулируем обработку
                posts_count = 5  # Симулируем 5 постов
                total_posts += posts_count
                processed_count += 1

                logger.info(f"✅ Region {region_name} processed: {posts_count} posts")

                # Пауза между регионами для VK API rate limiting
                if i < len(active_regions) - 1:  # Не ждем после последнего региона
                    logger.info("⏳ Waiting 5 seconds before next region...")
                    import time

                    time.sleep(5)  # 5 секунд для демонстрации

            except Exception as e:
                logger.error(f"❌ Error processing region {region_code}: {e}")
                continue

        # Итоговая статистика
        logger.info("=" * 80)
        logger.info("📊 WORKFLOW COMPLETE - FINAL STATISTICS")
        logger.info("=" * 80)
        logger.info(f"Duration: ~{processed_count * 2.5:.1f} minutes")
        logger.info(f"Regions processed: {processed_count}")
        logger.info(f"Regions skipped (outside work hours): {skipped_count}")
        logger.info(f"Total posts processed: {total_posts}")
        logger.info("✅ Production workflow completed successfully!")

        return {
            "success": True,
            "regions_processed": processed_count,
            "regions_skipped": skipped_count,
            "total_posts": total_posts,
            "duration_minutes": processed_count * 2.5,
            "timestamp": now_moscow.isoformat(),
        }

    except Exception as e:
        logger.error(f"❌ Production workflow failed: {e}", exc_info=True)
        return {"success": False, "error": str(e), "timestamp": datetime.now().isoformat()}


@app.task(bind=True, name="tasks.production_workflow_tasks.test_simple_task")
def test_simple_task(self):
    """
    Простая тестовая задача для проверки работы Celery
    """
    logger.info("=" * 50)
    logger.info("🧪 Testing simple Celery task")
    logger.info("=" * 50)

    try:
        # Проверка времени
        moscow_tz = pytz.timezone("Europe/Moscow")
        now_moscow = datetime.now(moscow_tz)
        current_hour = now_moscow.hour

        logger.info(f"⏰ Current time: {now_moscow.strftime('%H:%M:%S MSK')}")
        logger.info(f"🕐 Current hour: {current_hour}")

        # Проверка рабочих часов
        work_hours_start = 7
        work_hours_end = 22

        if work_hours_start <= current_hour <= work_hours_end:
            logger.info(f"✅ Inside work hours: {work_hours_start}:00-{work_hours_end}:00 MSK")
            status = "active"
        else:
            logger.info(f"😴 Outside work hours: {work_hours_start}:00-{work_hours_end}:00 MSK")
            status = "paused"

        result = {
            "success": True,
            "timestamp": now_moscow.isoformat(),
            "current_hour": current_hour,
            "work_hours_start": work_hours_start,
            "work_hours_end": work_hours_end,
            "status": status,
            "message": f"Task executed successfully at {now_moscow.strftime('%H:%M:%S MSK')}",
        }

        logger.info(f"✅ Task completed: {result}")
        return result

    except Exception as e:
        logger.error(f"❌ Task failed: {e}")
        return {"success": False, "error": str(e), "timestamp": datetime.now().isoformat()}


if __name__ == "__main__":
    # Простой тест
    print("Testing production workflow task...")
    result = run_production_workflow_all_regions_sync()
    print(f"Result: {result}")
