"""
Контентные фильтры - проверка содержимого
Уровни 4-6 из Postopus: дедупликация, медиа, черные списки
"""
import logging
import re
from typing import Any, Set
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .base import FastFilter, DBFilter, FilterResult
from database.models import Post, Filter

logger = logging.getLogger(__name__)


class TextDuplicateFilter(DBFilter):
    """
    Проверка текстовых дубликатов через "рафинад"
    
    Из Postopus CORE_CONCEPTS:
    "Рафинад - нормализованный текст для сравнения"
    "Сердцевина текста (20-70%) работает лучше всего"
    """
    
    def __init__(self, check_full: bool = True, check_core: bool = True):
        super().__init__(name="Text Duplicate Check", priority=40)
        self.check_full = check_full
        self.check_core = check_core
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка на текстовый дубликат"""
        session = context.get('session')
        
        if not session or not hasattr(post, 'text') or not post.text:
            return FilterResult(passed=True)
        
        # Проверка полного рафинада
        if self.check_full and hasattr(post, 'fingerprint_text'):
            text_hash = post.fingerprint_text
            
            if text_hash:
                result = await session.execute(
                    select(Post.id).where(
                        Post.fingerprint_text == text_hash,
                        Post.id != getattr(post, 'id', None)
                    )
                )
                duplicate = result.scalar()
                
                if duplicate:
                    return FilterResult(
                        passed=False,
                        reason=f"Text duplicate (full): matches post {duplicate}"
                    )
        
        # Проверка сердцевины текста (более точная)
        if self.check_core and hasattr(post, 'fingerprint_text_core'):
            core_hash = post.fingerprint_text_core
            
            if core_hash:
                result = await session.execute(
                    select(Post.id).where(
                        Post.fingerprint_text_core == core_hash,
                        Post.id != getattr(post, 'id', None)
                    )
                )
                duplicate = result.scalar()
                
                if duplicate:
                    return FilterResult(
                        passed=False,
                        reason=f"Text duplicate (core): matches post {duplicate}"
                    )
        
        return FilterResult(passed=True)


class MediaDuplicateFilter(DBFilter):
    """
    Проверка дубликатов медиа (фото/видео)
    
    Из Postopus: hash = [photo_id, video_id, ...]
    """
    
    def __init__(self):
        super().__init__(name="Media Duplicate Check", priority=41)
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка на дубликаты медиа"""
        session = context.get('session')
        
        if not session or not hasattr(post, 'fingerprint_media'):
            return FilterResult(passed=True)
        
        media_fingerprint = post.fingerprint_media
        
        if not media_fingerprint or len(media_fingerprint) == 0:
            # Нет медиа - пропускаем проверку
            return FilterResult(passed=True)
        
        # Ищем посты с такими же медиа ID
        # Это JSON поле, поэтому используем оператор @>
        result = await session.execute(
            select(Post.id).where(
                Post.fingerprint_media.contains(media_fingerprint),
                Post.id != getattr(post, 'id', None)
            )
        )
        duplicate = result.scalar()
        
        if duplicate:
            return FilterResult(
                passed=False,
                reason=f"Media duplicate: matches post {duplicate}"
            )
        
        return FilterResult(passed=True)


class BlacklistWordFilter(DBFilter):
    """
    Фильтр по черному списку слов
    
    Из Postopus: delete_msg_blacklist (1177 слов!)
    """
    
    def __init__(self):
        super().__init__(name="Blacklist Word Check", priority=50)
        self._blacklist_cache = None
        self._cache_time = None
        self._cache_ttl = 300  # 5 минут
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка на запрещенные слова"""
        session = context.get('session')
        
        if not session or not hasattr(post, 'text') or not post.text:
            return FilterResult(passed=True)
        
        # Получить черный список
        blacklist = await self._get_blacklist(session)
        
        text_lower = post.text.lower()
        
        # Проверить каждое слово
        for word in blacklist:
            if word in text_lower:
                return FilterResult(
                    passed=False,
                    reason=f"Blacklist word: '{word}'",
                    metadata={'blacklist_word': word}
                )
        
        return FilterResult(passed=True)
    
    async def _get_blacklist(self, session: AsyncSession) -> Set[str]:
        """Получить черный список с кэшированием"""
        now = datetime.utcnow()
        
        # Проверить кэш
        if (self._blacklist_cache is not None and 
            self._cache_time is not None and
            (now - self._cache_time).total_seconds() < self._cache_ttl):
            return self._blacklist_cache
        
        # Загрузить из БД
        result = await session.execute(
            select(Filter.pattern).where(
                Filter.type == 'blacklist_word',
                Filter.is_active == True,
                Filter.action == 'delete'
            )
        )
        
        patterns = result.scalars().all()
        blacklist = set(p.lower() for p in patterns if p)
        
        # Обновить кэш
        self._blacklist_cache = blacklist
        self._cache_time = now
        
        logger.debug(f"Loaded {len(blacklist)} blacklist words")
        
        return blacklist


class TextLengthFilter(FastFilter):
    """
    Фильтр по длине текста
    
    Из Postopus: Короткие посты без фото → в категорию "безфото"
    Очень длинные посты (> 10000) обычно копипаста
    """
    
    def __init__(self, min_length: int = 10, max_length: int = 10000):
        super().__init__(name="Text Length Check", priority=30)
        self.min_length = min_length
        self.max_length = max_length
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка длины текста"""
        if not hasattr(post, 'text') or not post.text:
            # Пост без текста - проверим есть ли медиа
            has_media = False
            if hasattr(post, 'attachments') and post.attachments:
                has_media = True
            elif hasattr(post, 'fingerprint_media') and post.fingerprint_media:
                has_media = True
            
            if not has_media:
                return FilterResult(
                    passed=False,
                    reason="No text and no media"
                )
            
            # Есть медиа без текста - разрешаем
            return FilterResult(passed=True)
        
        length = len(post.text)
        
        if length < self.min_length:
            return FilterResult(
                passed=False,
                reason=f"Text too short: {length} chars (min {self.min_length})"
            )
        
        if length > self.max_length:
            return FilterResult(
                passed=False,
                reason=f"Text too long: {length} chars (max {self.max_length})",
                metadata={'suspicious_copy_pasta': True}
            )
        
        return FilterResult(passed=True)


class SpamPatternFilter(FastFilter):
    """
    Фильтр спам-паттернов (регулярные выражения)
    
    Общие паттерны спама:
    - Номера телефонов в начале
    - Повторяющиеся символы
    - Заглавные буквы (CAPS LOCK)
    """
    
    def __init__(self):
        super().__init__(name="Spam Pattern Check", priority=51)
        
        # Паттерны спама
        self.spam_patterns = [
            (r'^\+?[78]\d{10}', 'Phone number at start'),
            (r'[А-ЯA-Z]{20,}', 'Too many CAPS'),
            (r'(.)\1{10,}', 'Repeating characters'),
            (r'💰|💵|💳|💸', 'Money emojis (often spam)'),
            (r'http[s]?://bit\.ly|goo\.gl|clck\.ru', 'Short URL (often spam)'),
        ]
    
    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка на спам-паттерны"""
        if not hasattr(post, 'text') or not post.text:
            return FilterResult(passed=True)
        
        text = post.text
        
        for pattern, description in self.spam_patterns:
            if re.search(pattern, text):
                return FilterResult(
                    passed=False,
                    reason=f"Spam pattern: {description}",
                    metadata={'pattern': pattern}
                )
        
        return FilterResult(passed=True)

