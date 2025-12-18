"""
Smart Scheduler - интеллектуальное планирование публикаций
Анализирует исторический engagement для определения оптимального времени
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal
from database.models import Post, Region, PublishSchedule

logger = logging.getLogger(__name__)


class SmartScheduler:
    """
    Умный планировщик публикаций
    
    Определяет оптимальное время публикации на основе:
    - Исторического engagement (views, likes, reposts)
    - Дня недели
    - Времени суток
    - Категории контента
    """
    
    # Оптимальный микс категорий по времени суток
    OPTIMAL_MIX = {
        'morning': {  # 6:00-12:00
            'novost': 0.4,
            'admin': 0.2,
            'kultura': 0.15,
            'sport': 0.15,
            'sosed': 0.1
        },
        'afternoon': {  # 12:00-18:00
            'novost': 0.35,
            'admin': 0.15,
            'kultura': 0.2,
            'sport': 0.2,
            'sosed': 0.1
        },
        'evening': {  # 18:00-22:00
            'novost': 0.3,
            'admin': 0.1,
            'kultura': 0.25,
            'sport': 0.25,
            'sosed': 0.1
        }
    }
    
    async def analyze_engagement_by_hour(
        self,
        region_code: str,
        days_back: int = 90
    ) -> Dict[int, float]:
        """
        Анализ engagement по часам дня
        
        Args:
            region_code: Код региона
            days_back: Сколько дней истории анализировать
            
        Returns:
            Dict {hour: avg_engagement}
        """
        async with AsyncSessionLocal() as session:
            # Запрос статистики по часам
            query = text("""
                SELECT 
                    EXTRACT(HOUR FROM date_published) as hour,
                    COUNT(*) as post_count,
                    AVG(views) as avg_views,
                    AVG(likes) as avg_likes,
                    AVG(reposts) as avg_reposts,
                    AVG(views + likes * 2 + reposts * 5) as avg_engagement
                FROM posts
                WHERE region_id = (SELECT id FROM regions WHERE code = :code)
                  AND date_published > NOW() - INTERVAL '1 day' * :days
                  AND published_vk = true
                GROUP BY hour
                ORDER BY hour
            """)
            
            result = await session.execute(
                query,
                {"code": region_code, "days": days_back}
            )
            
            engagement_by_hour = {}
            for row in result:
                hour = int(row[0])
                avg_engagement = float(row[5]) if row[5] else 0
                engagement_by_hour[hour] = avg_engagement
            
            logger.info(
                f"Analyzed engagement for {region_code}: "
                f"{len(engagement_by_hour)} hours with data"
            )
            
            return engagement_by_hour
    
    async def analyze_engagement_by_day_of_week(
        self,
        region_code: str,
        days_back: int = 90
    ) -> Dict[int, float]:
        """
        Анализ engagement по дням недели
        
        Args:
            region_code: Код региона
            days_back: Сколько дней истории
            
        Returns:
            Dict {day_of_week: avg_engagement} (0=Monday, 6=Sunday)
        """
        async with AsyncSessionLocal() as session:
            query = text("""
                SELECT 
                    EXTRACT(DOW FROM date_published) as day_of_week,
                    COUNT(*) as post_count,
                    AVG(views + likes * 2 + reposts * 5) as avg_engagement
                FROM posts
                WHERE region_id = (SELECT id FROM regions WHERE code = :code)
                  AND date_published > NOW() - INTERVAL '1 day' * :days
                  AND published_vk = true
                GROUP BY day_of_week
                ORDER BY day_of_week
            """)
            
            result = await session.execute(
                query,
                {"code": region_code, "days": days_back}
            )
            
            engagement_by_day = {}
            for row in result:
                day = int(row[0])
                avg_engagement = float(row[2]) if row[2] else 0
                engagement_by_day[day] = avg_engagement
            
            return engagement_by_day
    
    async def get_optimal_time(
        self,
        region_code: str,
        category: str = 'novost',
        prefer_time_slot: str = None
    ) -> Tuple[int, int]:
        """
        Определить оптимальное время для публикации
        
        Args:
            region_code: Код региона
            category: Категория контента
            prefer_time_slot: Предпочтительное время ('morning', 'afternoon', 'evening')
            
        Returns:
            (hour, minute) - оптимальное время
        """
        # Получить статистику по часам
        engagement_by_hour = await self.analyze_engagement_by_hour(region_code)
        
        if not engagement_by_hour:
            # Нет исторических данных, используем defaults
            logger.warning(f"No historical data for {region_code}, using defaults")
            return (12, 0)  # Default: полдень
        
        # Фильтровать по time slot если указан
        if prefer_time_slot:
            hour_ranges = {
                'morning': range(6, 12),
                'afternoon': range(12, 18),
                'evening': range(18, 23)
            }
            hours_in_slot = hour_ranges.get(prefer_time_slot, range(0, 24))
            
            # Фильтровать engagement только для нужного slot
            filtered_engagement = {
                h: eng for h, eng in engagement_by_hour.items()
                if h in hours_in_slot
            }
            
            if filtered_engagement:
                engagement_by_hour = filtered_engagement
        
        # Найти час с максимальным engagement
        best_hour = max(engagement_by_hour, key=engagement_by_hour.get)
        
        logger.info(
            f"Optimal time for {region_code}: {best_hour}:00 "
            f"(engagement: {engagement_by_hour[best_hour]:.1f})"
        )
        
        # Минуты можно варьировать (0, 15, 30, 45)
        # Выбираем случайно для разнообразия
        import random
        minute = random.choice([0, 15, 30, 45])
        
        return (best_hour, minute)
    
    async def schedule_publication(
        self,
        digest_id: int,
        region_code: str,
        category: str = 'novost',
        scheduled_time: Optional[datetime] = None
    ) -> Dict:
        """
        Запланировать публикацию дайджеста
        
        Args:
            digest_id: ID дайджеста
            region_code: Код региона
            category: Категория
            scheduled_time: Желаемое время (если None, определится автоматически)
            
        Returns:
            Dict с информацией о планировании
        """
        if scheduled_time is None:
            # Определить оптимальное время
            hour, minute = await self.get_optimal_time(region_code, category)
            
            # Создать datetime для следующего occurrence
            now = datetime.now()
            scheduled_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            
            # Если время уже прошло сегодня, взять завтра
            if scheduled_time <= now:
                scheduled_time += timedelta(days=1)
        
        # TODO: Сохранить в БД таблицу scheduled_publications
        # (когда она будет создана)
        
        logger.info(
            f"Scheduled digest {digest_id} for {region_code} "
            f"at {scheduled_time.strftime('%Y-%m-%d %H:%M')}"
        )
        
        return {
            "digest_id": digest_id,
            "region_code": region_code,
            "category": category,
            "scheduled_time": scheduled_time.isoformat(),
            "optimal": scheduled_time is None
        }
    
    async def get_engagement_report(
        self,
        region_code: str,
        days_back: int = 90
    ) -> Dict:
        """
        Получить полный отчёт по engagement
        
        Args:
            region_code: Код региона
            days_back: Период анализа в днях
            
        Returns:
            Dict с детальной статистикой
        """
        engagement_by_hour = await self.analyze_engagement_by_hour(region_code, days_back)
        engagement_by_day = await self.analyze_engagement_by_day_of_week(region_code, days_back)
        
        # Определить best/worst часы
        if engagement_by_hour:
            best_hour = max(engagement_by_hour, key=engagement_by_hour.get)
            worst_hour = min(engagement_by_hour, key=engagement_by_hour.get)
        else:
            best_hour, worst_hour = None, None
        
        # Определить best/worst дни
        if engagement_by_day:
            best_day = max(engagement_by_day, key=engagement_by_day.get)
            worst_day = min(engagement_by_day, key=engagement_by_day.get)
        else:
            best_day, worst_day = None, None
        
        # Названия дней
        day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
        
        return {
            "region_code": region_code,
            "period_days": days_back,
            "engagement_by_hour": engagement_by_hour,
            "engagement_by_day": engagement_by_day,
            "best_hour": best_hour,
            "worst_hour": worst_hour,
            "best_day": best_day,
            "best_day_name": day_names[best_day] if best_day is not None else None,
            "worst_day": worst_day,
            "worst_day_name": day_names[worst_day] if worst_day is not None else None,
            "recommendations": await self._generate_recommendations(
                engagement_by_hour, engagement_by_day
            )
        }
    
    async def _generate_recommendations(
        self,
        engagement_by_hour: Dict[int, float],
        engagement_by_day: Dict[int, float]
    ) -> List[str]:
        """Генерация рекомендаций на основе анализа"""
        recommendations = []
        
        if engagement_by_hour:
            best_hour = max(engagement_by_hour, key=engagement_by_hour.get)
            worst_hour = min(engagement_by_hour, key=engagement_by_hour.get)
            
            best_eng = engagement_by_hour[best_hour]
            worst_eng = engagement_by_hour[worst_hour]
            
            if best_eng > worst_eng * 2:
                recommendations.append(
                    f"📊 Публикуйте в {best_hour}:00 - engagement в 2x выше, чем в {worst_hour}:00"
                )
            
            # Проверить вечерние часы
            evening_hours = [h for h in engagement_by_hour.keys() if 18 <= h <= 22]
            if evening_hours:
                avg_evening = sum(engagement_by_hour[h] for h in evening_hours) / len(evening_hours)
                avg_all = sum(engagement_by_hour.values()) / len(engagement_by_hour)
                
                if avg_evening > avg_all * 1.2:
                    recommendations.append(
                        "🌆 Вечерние часы (18:00-22:00) показывают на 20%+ лучший engagement"
                    )
        
        if engagement_by_day:
            best_day = max(engagement_by_day, key=engagement_by_day.get)
            day_names = ['Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота', 'Воскресенье']
            
            # Проверить weekend vs weekdays
            weekdays = [d for d in engagement_by_day.keys() if d < 5]
            weekend = [d for d in engagement_by_day.keys() if d >= 5]
            
            if weekdays and weekend:
                avg_weekday = sum(engagement_by_day[d] for d in weekdays) / len(weekdays)
                avg_weekend = sum(engagement_by_day[d] for d in weekend) / len(weekend)
                
                if avg_weekend > avg_weekday * 1.15:
                    recommendations.append(
                        "📅 Выходные дни показывают на 15%+ лучший engagement"
                    )
                elif avg_weekday > avg_weekend * 1.15:
                    recommendations.append(
                        "📅 Будние дни показывают на 15%+ лучший engagement"
                    )
        
        if not recommendations:
            recommendations.append("✅ Engagement распределён равномерно - можно публиковать в любое время")
        
        return recommendations
    
    async def find_next_available_slot(
        self,
        region_code: str,
        from_time: datetime,
        category: str = 'novost'
    ) -> datetime:
        """
        Найти следующий доступный слот для публикации
        
        Избегает:
        - Уже занятых временных слотов
        - Слишком частых публикаций (мин. 2 часа между)
        
        Args:
            region_code: Код региона
            from_time: Искать от этого времени
            category: Категория контента
            
        Returns:
            Следующее доступное время
        """
        # TODO: Проверить уже запланированные публикации
        # SELECT scheduled_time FROM scheduled_publications
        # WHERE region_code = :code AND scheduled_time > :from_time
        # ORDER BY scheduled_time
        
        # Пока просто возвращаем оптимальное время
        hour, minute = await self.get_optimal_time(region_code, category)
        
        next_time = from_time.replace(hour=hour, minute=minute, second=0, microsecond=0)
        
        if next_time <= from_time:
            next_time += timedelta(days=1)
        
        return next_time
    
    async def get_publication_calendar(
        self,
        region_code: str,
        days: int = 7
    ) -> List[Dict]:
        """
        Получить календарь публикаций на следующие N дней
        
        Args:
            region_code: Код региона
            days: Количество дней вперёд
            
        Returns:
            List of scheduled publications
        """
        calendar = []
        
        # TODO: Получить из БД запланированные публикации
        # SELECT * FROM scheduled_publications
        # WHERE region_code = :code
        #   AND scheduled_time BETWEEN NOW() AND NOW() + INTERVAL ':days days'
        # ORDER BY scheduled_time
        
        # Пока возвращаем рекомендуемые слоты
        optimal_hours = await self.get_recommended_slots(region_code, days)
        
        for slot in optimal_hours:
            calendar.append({
                "time": slot["time"].isoformat(),
                "hour": slot["hour"],
                "day_of_week": slot["day_of_week"],
                "recommended_category": slot["category"],
                "expected_engagement": slot["engagement"],
                "status": "available"
            })
        
        return calendar
    
    async def get_recommended_slots(
        self,
        region_code: str,
        days: int = 7
    ) -> List[Dict]:
        """
        Получить рекомендуемые слоты для публикаций
        
        Args:
            region_code: Код региона
            days: Сколько дней вперёд
            
        Returns:
            List рекомендуемых слотов
        """
        engagement_by_hour = await self.analyze_engagement_by_hour(region_code)
        
        if not engagement_by_hour:
            # Нет данных, используем стандартные слоты
            engagement_by_hour = {
                9: 100, 12: 120, 15: 110, 18: 150, 21: 140
            }
        
        # Найти топ-3 часа
        top_hours = sorted(
            engagement_by_hour.items(),
            key=lambda x: x[1],
            reverse=True
        )[:3]
        
        slots = []
        now = datetime.now()
        
        for day in range(days):
            date = now + timedelta(days=day)
            
            for hour, engagement in top_hours:
                slot_time = date.replace(hour=hour, minute=0, second=0, microsecond=0)
                
                if slot_time > now:
                    # Определить time slot и рекомендуемую категорию
                    if 6 <= hour < 12:
                        time_slot = 'morning'
                    elif 12 <= hour < 18:
                        time_slot = 'afternoon'
                    else:
                        time_slot = 'evening'
                    
                    # Получить лучшую категорию для этого времени
                    category_mix = self.OPTIMAL_MIX[time_slot]
                    best_category = max(category_mix, key=category_mix.get)
                    
                    slots.append({
                        "time": slot_time,
                        "hour": hour,
                        "day_of_week": slot_time.weekday(),
                        "time_slot": time_slot,
                        "category": best_category,
                        "engagement": engagement
                    })
        
        # Сортировать по времени
        slots.sort(key=lambda x: x["time"])
        
        return slots
    
    async def should_publish_now(
        self,
        region_code: str,
        category: str = 'novost',
        tolerance_hours: int = 2
    ) -> Tuple[bool, str]:
        """
        Проверить, стоит ли публиковать сейчас
        
        Args:
            region_code: Код региона
            category: Категория контента
            tolerance_hours: Допустимое отклонение в часах
            
        Returns:
            (should_publish, reason)
        """
        now = datetime.now()
        current_hour = now.hour
        
        # Получить оптимальное время
        optimal_hour, _ = await self.get_optimal_time(region_code, category)
        
        # Проверить, близко ли текущее время к оптимальному
        hour_diff = abs(current_hour - optimal_hour)
        
        if hour_diff <= tolerance_hours:
            return True, f"Сейчас хорошее время (optimal: {optimal_hour}:00, current: {current_hour}:00)"
        else:
            return False, f"Лучше подождать до {optimal_hour}:00 (текущее: {current_hour}:00, разница: {hour_diff}h)"
    
    async def get_engagement_forecast(
        self,
        region_code: str,
        publish_time: datetime
    ) -> Dict:
        """
        Прогноз engagement для конкретного времени публикации
        
        Args:
            region_code: Код региона
            publish_time: Предполагаемое время публикации
            
        Returns:
            Прогноз engagement
        """
        engagement_by_hour = await self.analyze_engagement_by_hour(region_code)
        engagement_by_day = await self.analyze_engagement_by_day_of_week(region_code)
        
        hour = publish_time.hour
        day_of_week = publish_time.weekday()
        
        hour_engagement = engagement_by_hour.get(hour, 0)
        day_engagement = engagement_by_day.get(day_of_week, 0)
        
        # Комбинированный прогноз (60% час, 40% день недели)
        forecast = hour_engagement * 0.6 + day_engagement * 0.4
        
        # Получить среднее для сравнения
        if engagement_by_hour:
            avg_engagement = sum(engagement_by_hour.values()) / len(engagement_by_hour)
            vs_average = ((forecast - avg_engagement) / avg_engagement) * 100
        else:
            avg_engagement = 0
            vs_average = 0
        
        return {
            "publish_time": publish_time.isoformat(),
            "forecast_engagement": round(forecast, 1),
            "average_engagement": round(avg_engagement, 1),
            "vs_average_pct": round(vs_average, 1),
            "recommendation": (
                "✅ Отличное время!" if vs_average > 10 else
                "⚠️ Среднее время" if vs_average > -10 else
                "❌ Плохое время, лучше выбрать другое"
            )
        }


if __name__ == "__main__":
    import asyncio
    
    async def test():
        scheduler = SmartScheduler()
        
        print("="*60)
        print("🧪 Testing Smart Scheduler")
        print("="*60)
        
        region_code = "mi"
        
        # Test 1: Engagement by hour
        print("\n1. Analyzing engagement by hour...")
        engagement = await scheduler.analyze_engagement_by_hour(region_code)
        if engagement:
            print(f"   Found data for {len(engagement)} hours")
            top_3 = sorted(engagement.items(), key=lambda x: x[1], reverse=True)[:3]
            print(f"   Top 3 hours:")
            for hour, eng in top_3:
                print(f"     {hour}:00 - engagement: {eng:.1f}")
        else:
            print("   No historical data")
        
        # Test 2: Optimal time
        print("\n2. Getting optimal time...")
        hour, minute = await scheduler.get_optimal_time(region_code)
        print(f"   Optimal time: {hour}:{minute:02d}")
        
        # Test 3: Should publish now?
        print("\n3. Should publish now?...")
        should, reason = await scheduler.should_publish_now(region_code)
        print(f"   {should}: {reason}")
        
        # Test 4: Engagement report
        print("\n4. Getting engagement report...")
        report = await scheduler.get_engagement_report(region_code, days_back=30)
        print(f"   Best hour: {report['best_hour']}:00")
        print(f"   Best day: {report['best_day_name']}")
        print(f"   Recommendations:")
        for rec in report['recommendations']:
            print(f"     - {rec}")
        
        print("\n✅ Test completed!")
    
    asyncio.run(test())

