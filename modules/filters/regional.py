"""
Региональные фильтры
Проверка релевантности контента для региона
"""

import logging
import time
from typing import Any, Optional, Set, Tuple

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Region
from database.models_extended import RegionConfig

from .base import DBFilter, FastFilter, FilterResult
from .morphology import find_matching_keywords, get_word_stem

logger = logging.getLogger(__name__)


# Слова из названий регионов, которые не несут смысла для релевантности
_GENERIC_NAME_TOKENS = {
    "ИНФО",
    "INFO",
    "НОВОСТИ",
    "NEWS",
    "ГРУППА",
    "ОБЛАСТЬ",
    "РАЙОН",
    "ГОРОД",
    "ПОСЁЛОК",
    "ПОСЕЛОК",
    "СЕЛО",
}


class RegionalRelevanceFilter(DBFilter):
    """
    Фильтр релевантности для региона

    Из Postopus CORE_CONCEPTS:
    "Один и тот же контент может быть релевантен для одного региона
     и бесполезен для другого"

    "Региональность - это контекст, а не фильтр"

    Источники ключевых слов:
    1. ``RegionConfig.region_words`` (JSON {label: [words]}, мигрировано из
       MongoDB ``kirov_words`` + ``tatar_words``).
    2. ``RegionConfig.localities`` (JSON list, новое поле — населённые
       пункты района) — если присутствует.
    3. Запасной вариант: токены из ``Region.name`` и ``Region.code``,
       если ``RegionConfig`` пуст или отсутствует.

    Морфология — `modules.filters.morphology`: проверка идёт по стему,
    так что keyword «Малмыж» матчит «Малмыжский», «Малмыжем», «Малмыжского».
    """

    # TTL кеша ключевых слов на регион (секунды). RegionConfig обновляется
    # из UI редко — 5 минут достаточно, чтобы не дёргать БД на каждый пост.
    _CACHE_TTL_SECONDS = 300

    def __init__(self, required_matches: int = 1):
        super().__init__(name="Regional Relevance Check", priority=60)
        self.required_matches = required_matches
        # region_id -> (keywords_set, loaded_at_timestamp)
        self._keywords_cache: dict[int, Tuple[Set[str], float]] = {}

    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка релевантности для региона"""
        session: Optional[AsyncSession] = context.get("session")

        region, region_id = self._resolve_region(context)

        # Если нет контекста региона - пропускаем проверку
        if not session or (not region and not region_id):
            return FilterResult(passed=True)

        if not hasattr(post, "text") or not post.text:
            return FilterResult(passed=True)

        # Получить ключевые слова региона
        keywords = await self._get_region_keywords(session, region=region, region_id=region_id)

        if not keywords:
            # Нет настроенных ключевых слов - пропускаем
            return FilterResult(passed=True)

        matches = find_matching_keywords(post.text, keywords)

        if len(matches) >= self.required_matches:
            # Бонус за высокую релевантность
            score_modifier = min(len(matches) * 5, 20)

            return FilterResult(
                passed=True, score_modifier=score_modifier, metadata={"regional_matches": matches}
            )
        else:
            return FilterResult(
                passed=False,
                reason=f"Not regionally relevant (found {len(matches)} matches, need {self.required_matches})",  # noqa: E501
                metadata={"matches": matches},
            )

    @staticmethod
    def _resolve_region(context: dict) -> Tuple[Optional[Region], Optional[int]]:
        """Извлечь Region и region_id из контекста.

        В разных пайплайнах контекст приходит по-разному: production workflow
        кладёт ``region`` (объект), legacy-код — ``region_id``. Поддерживаем оба.
        """
        region = context.get("region")
        region_id = context.get("region_id")
        if region is None:
            return None, region_id
        if region_id is None:
            region_id = getattr(region, "id", None)
        return region, region_id

    async def _get_region_keywords(
        self,
        session: AsyncSession,
        region: Optional[Region] = None,
        region_id: Optional[int] = None,
    ) -> Set[str]:
        """Получить полный набор ключевых слов региона (с кешированием)."""
        cache_key = region_id if region_id is not None else getattr(region, "id", None)
        now = time.monotonic()

        if cache_key is not None:
            cached = self._keywords_cache.get(cache_key)
            if cached and (now - cached[1]) < self._CACHE_TTL_SECONDS:
                return cached[0]

        # Догружаем регион, если в контексте был только id
        if region is None and region_id is not None:
            result = await session.execute(select(Region).where(Region.id == region_id))
            region = result.scalar_one_or_none()

        if region is None:
            return set()

        keywords: Set[str] = set()

        # 1. Базовые ключи из названия региона и его кода — нужны как минимальный
        #    fallback, если RegionConfig ещё не настроен.
        keywords.update(self._extract_name_keywords(region))

        # 2. Слова из RegionConfig: region_words (исторически kirov/tatar)
        #    + localities (новое поле, если оно есть в схеме).
        cfg_result = await session.execute(
            select(RegionConfig).where(RegionConfig.region_code == region.code)
        )
        cfg = cfg_result.scalar_one_or_none()
        if cfg is not None:
            keywords.update(self._extract_region_words(cfg.region_words))
            localities = getattr(cfg, "localities", None)
            if localities:
                keywords.update(self._coerce_string_list(localities))

        # Морфология применяется при матчинге (find_matching_keywords),
        # поэтому в кеше держим исходные формы — это упрощает отладку
        # и позволяет показать пользователю реальное совпадение.
        # Но:
        #   - отфильтруем слишком короткие токены (стем < 4 букв);
        #   - дедуплицируем по lowercase, чтобы «МАЛМЫЖ» (из Region.name)
        #     и «Малмыж» (из RegionConfig.region_words) не считались
        #     двумя разными совпадениями.
        deduped: dict[str, str] = {}
        for k in keywords:
            if not k or len(get_word_stem(k)) < 4:
                continue
            key = k.lower().replace("ё", "е")
            # При коллизии оставляем более «приятную» форму
            # (с буквой в верхнем регистре в начале — обычно именно её
            # сохранил админ в RegionConfig).
            existing = deduped.get(key)
            if existing is None or (existing.isupper() and not k.isupper()):
                deduped[key] = k
        keywords = set(deduped.values())

        if cache_key is not None:
            self._keywords_cache[cache_key] = (keywords, now)

        return keywords

    @staticmethod
    def _extract_name_keywords(region: Region) -> Set[str]:
        """Достать осмысленные токены из Region.name и Region.code."""
        out: Set[str] = set()
        if region.code:
            out.add(region.code.strip())
        if region.name:
            # Убираем суффикс «- Инфо», нормализуем разделители, разбиваем
            cleaned = (
                region.name.upper().replace(" - ИНФО", "").replace("-ИНФО", "").replace("-", " ")
            )
            for token in cleaned.split():
                token = token.strip()
                if not token or token in _GENERIC_NAME_TOKENS:
                    continue
                out.add(token)
        return out

    @staticmethod
    def _extract_region_words(region_words: Any) -> Set[str]:
        """Распаковать region_words из RegionConfig в плоский набор слов.

        Исторически структура из MongoDB — словарь ``{label: [words]}`` (от
        ``kirov_words`` + ``tatar_words``). На всякий случай поддерживаем и
        просто список строк.
        """
        if not region_words:
            return set()
        if isinstance(region_words, dict):
            collected: Set[str] = set()
            for v in region_words.values():
                collected.update(RegionalRelevanceFilter._coerce_string_list(v))
            return collected
        if isinstance(region_words, list):
            return RegionalRelevanceFilter._coerce_string_list(region_words)
        return set()

    @staticmethod
    def _coerce_string_list(value: Any) -> Set[str]:
        """Привести произвольный JSON-узел к набору строк."""
        if not value:
            return set()
        if isinstance(value, str):
            return {value.strip()} if value.strip() else set()
        if isinstance(value, (list, tuple, set)):
            return {str(v).strip() for v in value if v}
        return set()

    def invalidate_cache(self, region_id: Optional[int] = None) -> None:
        """Сбросить кеш ключевых слов (целиком или для конкретного региона)."""
        if region_id is None:
            self._keywords_cache.clear()
        else:
            self._keywords_cache.pop(region_id, None)


class NeighborRegionFilter(FastFilter):
    """
    Фильтр для постов из соседних регионов

    Из Postopus: sosed - посты соседей, но только с хештегом #Новости
    """

    def __init__(self, require_hashtag: bool = True):
        super().__init__(name="Neighbor Region Check", priority=61)
        self.require_hashtag = require_hashtag

    async def apply(self, post: Any, context: dict) -> FilterResult:
        """Проверка постов из соседних регионов"""
        is_neighbor = context.get("is_neighbor_region", False)

        if not is_neighbor:
            # Не из соседнего региона - пропускаем проверку
            return FilterResult(passed=True)

        if not self.require_hashtag:
            return FilterResult(passed=True)

        # Для соседей требуем хештег #Новости
        if not hasattr(post, "text") or not post.text:
            return FilterResult(passed=False, reason="Neighbor region post without text")

        text_lower = post.text.lower()
        news_hashtags = ["#новости", "#news", "новости"]

        has_hashtag = any(tag in text_lower for tag in news_hashtags)

        if not has_hashtag:
            return FilterResult(
                passed=False, reason="Neighbor region post without #Новости hashtag"
            )

        return FilterResult(passed=True, score_modifier=5)
