"""
AI Post Analyzer - analyzes VK posts for relevance and categorization.

**Ветка LLM убрана вместе с ключом Groq (D-024, 2026-08-12).** Она была
недостижима в проде: единственный живой вызывающий, ``modules/real_workflow.py``,
создаёт ``PostAnalyzer()`` БЕЗ ключа, то есть всегда шёл keyword-путь; второй,
``tasks/analysis_tasks.py``, не был зарегистрирован в Celery (отсутствовал в
``include``) и вдобавок не импортировался — он удалён тем же PR. Поэтому
удаление LLM-ветки не меняет ни одного прод-поведения.

Классификация здесь — ключевые слова + фильтры + ``SentimentAnalyzer``. Если
понадобится нейро-разметка постов, её надо строить на общем
``modules/deepseek_client.py``, а не воскрешать Groq-клиент.
"""

import logging
import time
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Filter, Post
from modules.ai_analyzer.sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)

_BLACKLIST_CACHE_TTL_SECONDS = 300
_blacklist_cache_ts: float = 0.0
_blacklist_cache_patterns: list[str] = []


class PostAnalyzer:
    """Analyzes posts using AI and filters"""

    def __init__(self):
        """Initialize Post Analyzer (keyword-based, см. docstring модуля)."""
        self.sentiment_analyzer = SentimentAnalyzer()

    async def analyze_post(self, post: Post, session: AsyncSession) -> Dict[str, Any]:
        """
        Analyze single post

        Args:
            post: Post object from database
            session: Database session

        Returns:
            Analysis results
        """
        if not post.text:
            return {"error": "No text to analyze"}

        logger.info(f"Analyzing post {post.id}: {post.text[:50]}...")

        # Check blacklist filters first
        is_spam, spam_reason = await self._check_filters(post.text, session)

        if is_spam:
            result = {
                "category": "reklama",
                "relevance": 0,
                "is_spam": True,
                "reason": spam_reason,
                "score": 0,
            }
        else:
            result = self._keyword_analysis(post.text)

        # Calculate final score
        score = self._calculate_score(result, post)
        result["score"] = score

        # NEW: Sentiment analysis
        sentiment = self.sentiment_analyzer.analyze(post.text)
        result["sentiment"] = sentiment

        # Update post with analysis
        post.ai_category = result.get("category", "novost")
        post.ai_relevance = result.get("relevance", 50)
        post.ai_score = score
        post.ai_analyzed = True
        post.ai_analysis_date = datetime.utcnow()
        post.is_spam = result.get("is_spam", False)

        # Update sentiment fields
        post.sentiment_label = sentiment["label"]
        post.sentiment_score = sentiment["score"]
        post.sentiment_emotions = sentiment["emotions"]

        # Update status
        if score >= 70:
            post.status = "approved"
        elif score >= 40:
            post.status = "analyzed"
        else:
            post.status = "rejected"

        return result

    async def _check_filters(self, text: str, session: AsyncSession) -> tuple[bool, Optional[str]]:
        """
        Check text against blacklist filters

        Returns:
            (is_spam, reason)
        """
        global _blacklist_cache_ts, _blacklist_cache_patterns

        # Cache active blacklist patterns to avoid repeated DB reads on every post
        now = time.monotonic()
        if (
            not _blacklist_cache_patterns
            or (now - _blacklist_cache_ts) > _BLACKLIST_CACHE_TTL_SECONDS
        ):
            result = await session.execute(
                select(Filter.pattern).where(
                    and_(Filter.type == "blacklist_word", Filter.is_active.is_(True))
                )
            )
            _blacklist_cache_patterns = [p for p in result.scalars().all() if p]
            _blacklist_cache_ts = now

        text_lower = text.lower()

        for pattern in _blacklist_cache_patterns:
            if pattern.lower() in text_lower:
                return True, f"Blacklist: {pattern}"

        return False, None

    def _keyword_analysis(self, text: str) -> Dict[str, Any]:
        """
        Simple keyword-based analysis (fallback)
        """
        text_lower = text.lower()

        # Keywords for categories
        categories_keywords = {
            "reklama": [
                "продам",
                "куплю",
                "продаю",
                "продаётся",
                "продается",
                "закажи",
                "заказать",
                "скидка",
                "акция",
                "цена",
                "руб",
            ],
            "admin": [
                "администрация",
                "постановление",
                "глава",
                "губернатор",
                "решение",
                "совет",
                "депутат",
            ],
            "kultura": [
                "концерт",
                "выставка",
                "библиотека",
                "музей",
                "театр",
                "фестиваль",
                "творчество",
            ],
            "sport": [
                "соревнования",
                "турнир",
                "спорт",
                "матч",
                "чемпионат",
                "тренировка",
                "секция",
            ],
            "detsad": ["детский сад", "дошкольное", "дети", "ребёнок", "воспитатель"],
            "sosed": ["район", "область", "регион", "соседи"],
        }

        # Count matches for each category
        scores = {}
        for category, keywords in categories_keywords.items():
            score = sum(1 for keyword in keywords if keyword in text_lower)
            scores[category] = score

        # Get category with max score
        if max(scores.values()) > 0:
            category = max(scores, key=scores.get)
        else:
            category = "novost"

        # Calculate relevance
        relevance = min(scores.get(category, 0) * 15 + 40, 100)

        # Check if spam
        is_spam = category == "reklama" and scores["reklama"] >= 2

        return {
            "category": category,
            "relevance": relevance,
            "is_spam": is_spam,
            "reason": "Keyword-based analysis",
        }

    def _calculate_score(self, analysis: Dict[str, Any], post: Post) -> int:
        """
        Calculate final post score (improved based on Postopus experience)

        Postopus insight: VK views are the MOST important signal!
        Platform already shows what people want to read.

        Scoring breakdown:
        - Engagement (VK metrics): 50 points (INCREASED from 30)
        - AI Relevance: 30 points (decreased from 50)
        - Recency: 20 points

        Total: 100 points
        """
        if analysis.get("is_spam"):
            return 0

        relevance = analysis.get("relevance", 50)

        # Engagement score (0-50 points) - KEY METRIC!
        # Views are most important (VK shows what people want)
        views_score = min((post.views / 50) * 25, 25)  # Up to 25 points from views
        likes_score = min((post.likes / 10) * 15, 15)  # Up to 15 points from likes
        reposts_score = min((post.reposts / 3) * 10, 10)  # Up to 10 points from reposts

        engagement_score = views_score + likes_score + reposts_score

        # Bonus for highly viral content (exponential boost)
        if post.views > 500:
            engagement_score = min(engagement_score * 1.2, 50)
        if post.reposts > 10:
            engagement_score = min(engagement_score * 1.1, 50)

        # Recency score (0-20 points)
        if post.date_published:
            age_hours = (datetime.utcnow() - post.date_published).total_seconds() / 3600
            if age_hours < 6:
                recency_score = 20  # Super fresh
            elif age_hours < 24:
                recency_score = 18
            elif age_hours < 48:
                recency_score = 12
            elif age_hours < 72:
                recency_score = 8
            else:
                recency_score = 3
        else:
            recency_score = 10

        # Total score (Engagement weight increased!)
        # Old: relevance * 0.5 + stats (30) + recency (20)
        # New: relevance * 0.3 + engagement (50) + recency (20)
        total = int(
            relevance * 0.3  # AI relevance (30% weight)
            + engagement_score  # VK engagement (50% weight)
            + recency_score  # Time factor (20% weight)
        )

        return min(total, 100)

    # Метод analyze_new_posts снят 2026-09-04 прогоном /deadcode: звавшие его
    # tasks/analysis_tasks.py (убран вместе с Groq, D-024) и correct_workflow
    # удалены, потребителя не осталось. Разметку потока ведёт HITL-классификатор
    # (modules/classifier), а не этот анализатор.
