"""
Digest Splitter — разделяет посты по тональности перед построением дайджеста

Разделяет посты на:
- mourning (траурные новости: смерть, гибель)
- regular (обычные новости: positive, neutral, negative)

Каждая группа затем передаётся в свой DigestBuilder.
"""

import logging
from typing import Any, Dict, List, Tuple

from modules.ai_analyzer.sentiment_analyzer import SentimentAnalyzer

logger = logging.getLogger(__name__)


class DigestSplitter:
    """
    Разделяет посты по тональности и создаёт отдельные дайджесты.

    Mourning-посты публиются отдельным постом без заголовка (только текст и хештеги),
    не перемешиваются с обычными новостями.
    """

    def __init__(self):
        self.analyzer = SentimentAnalyzer()

    def split_posts(
        self,
        posts: List[Dict[str, Any]],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Разделить посты на mourning и regular группы.

        Args:
            posts: Список постов (VK post data dicts)

        Returns:
            (mourning_posts, regular_posts) — два списка
        """
        mourning_posts = []
        regular_posts = []

        for post in posts:
            text = post.get("text", "") or ""
            if not text:
                # Если текста нет — в regular
                regular_posts.append(post)
                continue

            sentiment = self.analyzer.analyze(text)
            post["_sentiment"] = sentiment  # Cache for later use

            if sentiment["label"] == "mourning":
                mourning_posts.append(post)
            else:
                regular_posts.append(post)

        logger.info(
            f"Digest split: {len(mourning_posts)} mourning, "
            f"{len(regular_posts)} regular (total: {len(posts)})"
        )

        return mourning_posts, regular_posts

    def split_with_stats(
        self,
        posts: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Разделить посты и вернуть подробную статистику.

        Returns:
            Dict с:
            - mourning_posts, regular_posts
            - sentiment_distribution
            - mourning_markers_found
        """
        mourning_posts, regular_posts = self.split_posts(posts)

        # Distribution
        distribution = {
            "mourning": len(mourning_posts),
            "regular": len(regular_posts),
            "total": len(posts),
        }

        # What mourning markers were found?
        mourning_markers_found = []
        for post in mourning_posts:
            sentiment = post.get("_sentiment", {})
            wc = sentiment.get("word_counts", {})
            if wc.get("mourning", 0) > 0:
                mourning_markers_found.append(
                    {
                        "lip": f"{abs(post.get('owner_id', 0))}_{post.get('id', 0)}",
                        "mourning_count": wc["mourning"],
                    }
                )

        return {
            "mourning_posts": mourning_posts,
            "regular_posts": regular_posts,
            "distribution": distribution,
            "mourning_markers": mourning_markers_found,
        }
