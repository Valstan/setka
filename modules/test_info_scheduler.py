"""
Test-Info Scheduler - Расписание для региона "Тест-Инфо"

Специальное расписание с темами, которые перебираются по кругу каждые 5 минут.
"""
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
from enum import Enum
import json

from utils.timezone import now_moscow, format_moscow_time
from modules.test_info_post_collector import collect_test_info_posts_by_topic
from modules.publisher.vk_publisher import VKPublisher
from modules.region_config import RegionConfigManager
from modules.service_notifications import service_notifications
from modules.digest_template import (
    get_effective_digest_settings_for_region,
    load_region_by_code,
    parse_region_hashtags,
    topic_to_default_hashtag,
)
from modules.service_activity_notifier import (
    notify_post_collection_start,
    notify_post_collection_complete,
    notify_post_sorting_start,
    notify_post_sorting_complete,
    notify_digest_creation_start,
    notify_digest_creation_complete,
    notify_digest_publishing_start,
    notify_digest_publishing_complete,
)

logger = logging.getLogger(__name__)


class TestInfoTopic(Enum):
    """Темы для Тест-Инфо"""
    ADMINISTRATION = "Администрация"
    CULTURE = "Культура"
    SPORTS = "Спорт"
    NEWS = "Новости"
    EVENTS = "События"
    EDUCATION = "Образование"
    HEALTH = "Здоровье"
    BUSINESS = "Бизнес"


class TestInfoScheduler:
    """Расписание для региона Тест-Инфо"""
    
    def __init__(self):
        self.region_name = "Тест-Инфо"
        self.topics = [
            TestInfoTopic.ADMINISTRATION,
            TestInfoTopic.CULTURE,
            TestInfoTopic.SPORTS,
            TestInfoTopic.NEWS,
            TestInfoTopic.EVENTS,
            TestInfoTopic.EDUCATION,
            TestInfoTopic.HEALTH,
            TestInfoTopic.BUSINESS
        ]
        self.current_topic_index = 0
        self.last_execution_time = None
        self.execution_count = 0
        self.schedule_history = []
        self.max_history = 100
        
        # Настройки расписания
        self.execution_interval_minutes = 5
        self.posts_per_topic = 3  # Количество постов для дайджеста
        self.digest_length_min = 200  # Минимальная длина дайджеста
        # VK limit is 4096 chars; keep a little headroom for safety
        self.digest_length_max = 4096  # Максимальная длина дайджеста (VK limit)
        
        logger.info(f"Test-Info Scheduler initialized with {len(self.topics)} topics")
    
    def get_current_topic(self) -> TestInfoTopic:
        """Получить текущую тему"""
        return self.topics[self.current_topic_index]
    
    def get_next_topic(self) -> TestInfoTopic:
        """Получить следующую тему (циклически)"""
        next_index = (self.current_topic_index + 1) % len(self.topics)
        return self.topics[next_index]
    
    def advance_to_next_topic(self):
        """Перейти к следующей теме"""
        self.current_topic_index = (self.current_topic_index + 1) % len(self.topics)
        logger.info(f"Advanced to next topic: {self.get_current_topic().value}")
    
    def should_execute_now(self) -> bool:
        """Проверить, нужно ли выполнять задачу сейчас"""
        current_time = now_moscow()
        
        # Если это первый запуск
        if self.last_execution_time is None:
            return True
        
        # Проверяем интервал
        time_since_last = current_time - self.last_execution_time
        interval_minutes = self.execution_interval_minutes
        
        return time_since_last.total_seconds() >= (interval_minutes * 60)
    
    def get_time_until_next_execution(self) -> Optional[timedelta]:
        """Получить время до следующего выполнения"""
        if self.last_execution_time is None:
            return None
        
        current_time = now_moscow()
        next_execution = self.last_execution_time + timedelta(minutes=self.execution_interval_minutes)
        
        if next_execution > current_time:
            return next_execution - current_time
        
        return None
    
    async def execute_scheduled_task(self, vk_token: str) -> Dict[str, Any]:
        """Выполнить запланированную задачу"""
        current_time = now_moscow()
        current_topic = self.get_current_topic()
        
        logger.info("="*80)
        logger.info(f"🌙 Starting Test-Info scheduled task")
        logger.info(f"📅 Time: {format_moscow_time(current_time)}")
        logger.info(f"🎯 Topic: {current_topic.value}")
        logger.info(f"🔄 Execution #{self.execution_count + 1}")
        logger.info("="*80)
        
        try:
            # Уведомляем о начале сбора постов
            notify_post_collection_start(
                self.region_name, 
                current_topic.value, 
                communities_count=1  # Тест-Инфо группа
            )
            
            # Собираем реальные посты по теме из групп региона Тест-Инфо
            posts_data = await self._collect_real_posts(current_topic.value, vk_token)
            
            # Уведомляем о завершении сбора
            notify_post_collection_complete(
                self.region_name,
                current_topic.value,
                total_posts=len(posts_data),
                processing_time=1.2
            )
            
            if posts_data:
                # Уведомляем о начале сортировки
                notify_post_sorting_start(
                    self.region_name,
                    current_topic.value,
                    posts_count=len(posts_data)
                )
                
                # Симулируем сортировку постов
                approved_posts = await self._simulate_post_sorting(posts_data, current_topic.value)
                
                # Уведомляем о завершении сортировки
                notify_post_sorting_complete(
                    self.region_name,
                    current_topic.value,
                    approved_posts=len(approved_posts),
                    rejected_posts=len(posts_data) - len(approved_posts),
                    processing_time=0.8
                )
                
                if approved_posts:
                    # Уведомляем о начале создания дайджеста
                    notify_digest_creation_start(
                        self.region_name,
                        current_topic.value,
                        posts_count=len(approved_posts)
                    )
                    
                    # Создаем дайджест
                    digest_text = await self._create_digest(approved_posts, current_topic.value)
                    
                    # Уведомляем о завершении создания дайджеста
                    notify_digest_creation_complete(
                        self.region_name,
                        current_topic.value,
                        digest_length=len(digest_text),
                        processing_time=1.5
                    )
                    
                    # Уведомляем о начале публикации
                    notify_digest_publishing_start(
                        self.region_name,
                        current_topic.value,
                        channel="VK"
                    )
                    
                    publish_result = await self._publish_digest_to_main_group(
                        vk_token=vk_token,
                        digest_text=digest_text,
                        topic=current_topic.value,
                    )
                    
                    if publish_result.get("success"):
                        notify_digest_publishing_complete(
                            self.region_name,
                            current_topic.value,
                            channel="VK",
                            post_url=publish_result.get('url', ''),
                            processing_time=publish_result.get('time', 1.0)
                        )
                        
                        # Сохраняем результат
                        result = {
                            'success': True,
                            'topic': current_topic.value,
                            'posts_collected': len(posts_data),
                            'posts_approved': len(approved_posts),
                            'digest_length': len(digest_text),
                            'publish_url': publish_result.get('url', ''),
                            'execution_time': current_time.isoformat(),
                            'execution_number': self.execution_count + 1
                        }
                    else:
                        error_msg = publish_result.get("error", "unknown_publish_error")
                        logger.error(f"❌ Failed to publish digest: {error_msg}")
                        service_notifications.error(
                            f"Публикация дайджеста в VK не удалась: {error_msg}",
                            details={
                                "region": self.region_name,
                                "topic": current_topic.value,
                            },
                        )
                        result = {
                            'success': False,
                            'topic': current_topic.value,
                            'reason': 'publish_failed',
                            'error': error_msg,
                            'posts_collected': len(posts_data),
                            'posts_approved': len(approved_posts),
                            'digest_length': len(digest_text),
                            'publish_url': '',
                            'execution_time': current_time.isoformat(),
                            'execution_number': self.execution_count + 1
                        }
                else:
                    result = {
                        'success': False,
                        'topic': current_topic.value,
                        'reason': 'no_approved_posts',
                        'posts_collected': len(posts_data),
                        'execution_time': current_time.isoformat(),
                        'execution_number': self.execution_count + 1
                    }
            else:
                result = {
                    'success': False,
                    'topic': current_topic.value,
                    'reason': 'no_posts_found',
                    'execution_time': current_time.isoformat(),
                    'execution_number': self.execution_count + 1
                }
            
            # Обновляем состояние
            self.last_execution_time = current_time
            self.execution_count += 1
            
            # Сохраняем в историю
            self.schedule_history.append({
                'timestamp': current_time.isoformat(),
                'topic': current_topic.value,
                'result': result,
                'execution_number': self.execution_count
            })
            
            # Ограничиваем историю
            if len(self.schedule_history) > self.max_history:
                self.schedule_history = self.schedule_history[-self.max_history:]
            
            # Переходим к следующей теме
            self.advance_to_next_topic()
            
            logger.info("="*80)
            logger.info(f"✅ Test-Info scheduled task completed")
            logger.info(f"📊 Result: {result['success']}")
            logger.info(f"🎯 Next topic: {self.get_current_topic().value}")
            logger.info("="*80)
            
            return result
            
        except Exception as e:
            logger.error(f"❌ Error in Test-Info scheduled task: {e}", exc_info=True)
            
            # Обновляем состояние даже при ошибке
            self.last_execution_time = current_time
            self.execution_count += 1
            self.advance_to_next_topic()
            
            return {
                'success': False,
                'topic': current_topic.value,
                'error': str(e),
                'execution_time': current_time.isoformat(),
                'execution_number': self.execution_count
            }
    
    async def _collect_real_posts(self, topic: str, vk_token: str) -> List[Dict[str, Any]]:
        """Собрать реальные посты по теме из групп региона Тест-Инфо"""
        try:
            # Используем новый сборщик постов
            result = await collect_test_info_posts_by_topic(vk_token, topic)
            
            if result['success']:
                logger.info(f"✅ Собрано {result['posts_collected']} постов по теме '{topic}'")
                logger.info(f"📊 Статистика: {result['category_stats']}")
                
                # Преобразуем посты в нужный формат
                posts_data = []
                for post in result.get('posts', []):
                    # VK wall.get items обычно содержат owner_id (id сообщества) и id (id поста)
                    owner_id = post.get('owner_id')
                    if owner_id is None:
                        owner_id = post.get('source_vk_id')  # fallback (мы добавляем его в collector)
                    post_id = post.get('id')
                    post_url = ""
                    if owner_id is not None and post_id is not None:
                        post_url = f"https://vk.com/wall{owner_id}_{post_id}"

                    # Нормализуем метрики VK (они могут быть dict вида {"count": N})
                    def _count(v: Any) -> int:
                        if isinstance(v, dict):
                            return int(v.get("count") or 0)
                        try:
                            return int(v or 0)
                        except Exception:
                            return 0
                    
                    posts_data.append({
                        'id': post_id if post_id is not None else post.get('id', 'unknown'),
                        'owner_id': owner_id,
                        'url': post_url,
                        'text': post.get('text', ''),
                        'date': post.get('date', now_moscow().isoformat()),
                        'likes': _count(post.get('likes')),
                        'reposts': _count(post.get('reposts')),
                        'views': _count(post.get('views')),
                        'source_community': post.get('source_community', ''),
                        'source_category': post.get('source_category', '')
                    })
                
                return posts_data
            else:
                logger.error(f"❌ Ошибка при сборе постов: {result.get('error')}")
                return []
                
        except Exception as e:
            logger.error(f"❌ Исключение при сборе постов: {e}")
            return []

    async def _simulate_post_collection(self, topic: str) -> List[Dict[str, Any]]:
        """Симулировать сбор постов"""
        import asyncio
        await asyncio.sleep(0.5)  # Симуляция времени
        
        # Генерируем тестовые посты в зависимости от темы
        posts_templates = {
            "Администрация": [
                "Объявление о проведении общественных слушаний",
                "Информация о работе администрации района",
                "Новые нормативные акты и постановления"
            ],
            "Культура": [
                "Афиша культурных мероприятий",
                "Открытие новой выставки в музее",
                "Концерт местных артистов"
            ],
            "Спорт": [
                "Результаты спортивных соревнований",
                "Тренировки в спортивных секциях",
                "Строительство нового спортивного комплекса"
            ],
            "Новости": [
                "Важные новости района",
                "Интервью с местными жителями",
                "Обновления инфраструктуры"
            ],
            "События": [
                "Предстоящие мероприятия",
                "Праздничные мероприятия",
                "Общественные акции"
            ],
            "Образование": [
                "Новости из школ и детских садов",
                "Образовательные программы",
                "Достижения учащихся"
            ],
            "Здоровье": [
                "Информация о работе поликлиник",
                "Профилактические мероприятия",
                "Советы по здоровому образу жизни"
            ],
            "Бизнес": [
                "Новые предприятия в районе",
                "Поддержка малого бизнеса",
                "Экономические новости"
            ]
        }
        
        topic_posts = posts_templates.get(topic, ["Общая информация"])
        
        # Возвращаем случайное количество постов (1-5)
        import random
        num_posts = random.randint(1, 5)
        selected_posts = random.sample(topic_posts, min(num_posts, len(topic_posts)))
        
        posts = []
        for i, post_text in enumerate(selected_posts):
            posts.append({
                'id': f"test_post_{i+1}",
                'text': post_text,
                'topic': topic,
                'created_at': now_moscow().isoformat()
            })
        
        return posts
    
    async def _simulate_post_sorting(self, posts: List[Dict], topic: str) -> List[Dict]:
        """Симулировать сортировку постов"""
        import asyncio
        await asyncio.sleep(0.3)  # Симуляция времени
        
        # Простая логика: одобряем посты, которые содержат ключевые слова темы
        approved_posts = []
        topic_keywords = {
            "Администрация": ["администрация", "слушания", "нормативные"],
            "Культура": ["культура", "выставка", "концерт", "музей"],
            "Спорт": ["спорт", "соревнования", "тренировки"],
            "Новости": ["новости", "интервью", "инфраструктура"],
            "События": ["мероприятия", "праздник", "акции"],
            "Образование": ["школа", "образование", "учащиеся"],
            "Здоровье": ["здоровье", "поликлиника", "профилактика"],
            "Бизнес": ["бизнес", "предприятие", "экономика"]
        }
        
        keywords = topic_keywords.get(topic, [topic.lower()])
        
        for post in posts:
            post_text_lower = post['text'].lower()
            if any(keyword in post_text_lower for keyword in keywords):
                approved_posts.append(post)
        
        # Fallback для тестового региона: если ключевые слова не сработали,
        # берем несколько "лучших" постов по метрикам, чтобы не останавливать пайплайн.
        if not approved_posts:
            def _metric(v: Any) -> int:
                if isinstance(v, dict):
                    return int(v.get("count") or 0)
                try:
                    return int(v or 0)
                except Exception:
                    return 0

            candidates = [p for p in posts if (p.get("text") or "").strip()]
            candidates.sort(
                key=lambda p: (
                    _metric(p.get("views")),
                    _metric(p.get("likes")),
                    _metric(p.get("reposts")),
                ),
                reverse=True,
            )
            approved_posts = candidates[: max(self.posts_per_topic, 1)]
        
        return approved_posts
    
    async def _create_digest(self, posts: List[Dict], topic: str) -> str:
        """Создать дайджест из постов"""
        import asyncio
        await asyncio.sleep(0.4)  # Симуляция времени

        region_code = "test"
        settings_dict = await get_effective_digest_settings_for_region(region_code=region_code, topic=topic)
        # Fallback to safe defaults if region not found / config missing
        if not settings_dict:
            settings_dict = {
                "title": "📋 Госпаблики сообщают:",
                "footer": "",
                "include_source_links": True,
                "include_topic_hashtag": True,
                "include_region_hashtags": False,
                "topic_hashtag_override": topic_to_default_hashtag(topic),
            }

        title = (settings_dict.get("title") or "").strip()
        footer = (settings_dict.get("footer") or "").strip()
        include_source_links = bool(settings_dict.get("include_source_links", True))
        include_topic_hashtag = bool(settings_dict.get("include_topic_hashtag", True))
        include_region_hashtags = bool(settings_dict.get("include_region_hashtags", False))
        topic_hashtag = (settings_dict.get("topic_hashtag_override") or "").strip() or topic_to_default_hashtag(topic)

        digest_lines = []
        if title:
            digest_lines.append(title)
            digest_lines.append("")

        max_total_len = min(self.digest_length_max, 4096)
        current_len = len("\n".join(digest_lines))
        idx = 0
        for post in posts:
            if idx >= self.posts_per_topic:
                break
            text = (post.get('text') or "").strip()
            if not text:
                continue

            source = (post.get("source_community") or "").strip()
            url = (post.get("url") or "").strip()
            
            idx += 1

            # Требование: под новостью — название источника кликабельной ссылкой на оригинальный пост.
            # VK markup: [url|text]
            source_line = ""
            if include_source_links:
                if source and url:
                    source_line = f"[{url}|{source}]"
                elif url:
                    source_line = url
                elif source:
                    source_line = source

            line_parts = [f"{idx}. {text}"]
            if source_line:
                line_parts.append(source_line)
            line = "\n".join(line_parts)
            
            candidate_block = "\n".join([line, ""])
            # Проверяем общий лимит: если следующий пункт не влезает целиком — прекращаем
            if current_len + len(candidate_block) + 1 > max_total_len:
                idx -= 1  # откатываем счетчик, т.к. пункт не добавили
                break

            digest_lines.append(line)
            digest_lines.append("")  # пустая строка
            current_len = len("\n".join(digest_lines))

        # Footer + hashtags
        if footer:
            candidate = "\n".join([footer, ""])
            if current_len + len(candidate) + 1 <= max_total_len:
                digest_lines.append(footer)
                digest_lines.append("")
                current_len = len("\n".join(digest_lines))

        if include_topic_hashtag and topic_hashtag:
            if not topic_hashtag.startswith("#"):
                topic_hashtag = f"#{topic_hashtag}"
            candidate = "\n".join([topic_hashtag, ""])
            if current_len + len(candidate) + 1 <= max_total_len:
                digest_lines.append(topic_hashtag)
                digest_lines.append("")
                current_len = len("\n".join(digest_lines))

        if include_region_hashtags:
            region = await load_region_by_code(region_code)
            region_tags = parse_region_hashtags(region.local_hashtags if region else None)
            if region_tags:
                tags_line = " ".join(region_tags)
                candidate = "\n".join([tags_line, ""])
                if current_len + len(candidate) + 1 <= max_total_len:
                    digest_lines.append(tags_line)
                    digest_lines.append("")

        if idx == 0:
            logger.warning("Skipping digest creation: no posts with text")
            return ""

        return "\n".join(digest_lines).strip()

    async def _publish_digest_to_main_group(self, vk_token: str, digest_text: str, topic: str) -> Dict[str, Any]:
        """Реально опубликовать дайджест в главную группу региона test (Тест-Инфо)."""
        try:
            target_group_id = RegionConfigManager.get_main_group_id("test")
            if not target_group_id:
                return {
                    "success": False,
                    "error": "target_group_id_not_configured",
                }
            
            publisher = VKPublisher(vk_token)
            result = await publisher.publish_digest(
                text=digest_text,
                target_group_id=target_group_id,
                attachments=None,
                from_group=True,
            )
            
            # Нормализуем формат (в остальном коде ожидается time/url/success)
            normalized = {
                "success": bool(result.get("success")),
                "url": result.get("url", ""),
                "post_id": result.get("post_id"),
                "group_id": result.get("group_id", target_group_id),
                "time": 0.0,
            }
            if not normalized["success"]:
                normalized["error"] = result.get("error", "unknown_vk_publish_error")
            return normalized
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    def get_schedule_status(self) -> Dict[str, Any]:
        """Получить статус расписания"""
        current_time = now_moscow()
        time_until_next = self.get_time_until_next_execution()
        
        return {
            'region_name': self.region_name,
            'current_topic': self.get_current_topic().value,
            'next_topic': self.get_next_topic().value,
            'execution_interval_minutes': self.execution_interval_minutes,
            'last_execution_time': self.last_execution_time.isoformat() if self.last_execution_time else None,
            'next_execution_time': (self.last_execution_time + timedelta(minutes=self.execution_interval_minutes)).isoformat() if self.last_execution_time else None,
            'time_until_next_execution': str(time_until_next) if time_until_next else None,
            'execution_count': self.execution_count,
            'total_topics': len(self.topics),
            'topics_list': [topic.value for topic in self.topics],
            'current_time': current_time.isoformat(),
            'should_execute_now': self.should_execute_now()
        }
    
    def get_execution_history(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Получить историю выполнений"""
        recent = self.schedule_history[-limit:] if self.schedule_history else []
        return recent


# Глобальный экземпляр расписания
test_info_scheduler = TestInfoScheduler()


if __name__ == "__main__":
    # Тестирование расписания
    async def test_scheduler():
        print("🧪 Тестирование Test-Info Scheduler")
        print("=" * 50)
        
        # Показываем статус
        status = test_info_scheduler.get_schedule_status()
        print(f"Регион: {status['region_name']}")
        print(f"Текущая тема: {status['current_topic']}")
        print(f"Следующая тема: {status['next_topic']}")
        print(f"Интервал: {status['execution_interval_minutes']} минут")
        print(f"Всего тем: {status['total_topics']}")
        print(f"Темы: {', '.join(status['topics_list'])}")
        
        # Выполняем задачу
        print(f"\n🚀 Выполнение задачи...")
        result = await test_info_scheduler.execute_scheduled_task()
        
        print(f"\n📊 Результат:")
        print(f"Успех: {result['success']}")
        print(f"Тема: {result['topic']}")
        if result['success']:
            print(f"Постов собрано: {result['posts_collected']}")
            print(f"Постов одобрено: {result['posts_approved']}")
            print(f"Длина дайджеста: {result['digest_length']}")
            print(f"Ссылка: {result['publish_url']}")
        
        # Показываем обновленный статус
        print(f"\n📅 Обновленный статус:")
        status = test_info_scheduler.get_schedule_status()
        print(f"Текущая тема: {status['current_topic']}")
        print(f"Следующая тема: {status['next_topic']}")
        print(f"Количество выполнений: {status['execution_count']}")
        
        print("\n✅ Тест завершен!")
    
    import asyncio
    asyncio.run(test_scheduler())
