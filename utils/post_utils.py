"""
Post utilities migrated from old_postopus bin/utils/

Provides post ID generation, URL building, popularity scoring,
and other common post operations.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def lip_of_post(owner_id: int, post_id: int) -> str:
    """
    Generate unique post ID (lip).
    Migrated from old_postopus bin/utils/lip_of_post.py

    Format: "{abs(owner_id)}_{id}"
    Used for deduplication tracking.

    Args:
        owner_id: VK owner ID (usually negative for groups)
        post_id: VK post ID

    Returns:
        Unique lip string
    """
    return f"{abs(owner_id)}_{post_id}"


def post_popularity(views: int, likes: int, comments: int, reposts: int) -> float:
    """
    Calculate post popularity score.
    Migrated from old_postopus bin/utils/post_popularity.py

    Formula: (likes + comments*2 + reposts*3) / sqrt(views+1)
    This balances engagement with reach.

    Args:
        views: Number of views
        likes: Number of likes
        comments: Number of comments
        reposts: Number of reposts

    Returns:
        Popularity score (higher = more popular)
    """
    if views == 0 and likes == 0 and comments == 0 and reposts == 0:
        return 0.0

    engagement = likes + (comments * 2) + (reposts * 3)
    view_factor = math.sqrt(views + 1)

    return engagement / view_factor


def post_rating(
    views: Optional[int],
    likes: Optional[int],
    comments: Optional[int],
    reposts: Optional[int],
    *,
    alpha: float,
) -> Optional[float]:
    """Рейтинг поста для отбора в корневую группу (звено 5, шаг 1).

    ``score = (likes + 2*comments + 3*reposts) / (views + 1) ** alpha``

    Отличается от ``post_popularity`` ровно одним: показатель степени при
    просмотрах вынесен наружу. При ``alpha=0.5`` совпадает с ней с точностью
    до плавающей запятой (гейт в тестах) — то есть это не новая формула, а
    та же с ручкой. ``alpha`` меньше 0.5 поднимает охват, больше — отклик.

    ``views is None`` → ``None``, и это не то же самое, что ноль: пост, у
    которого просмотры не измерены, обогнал бы районный хит, потому что
    делитель схлопнулся бы в единицу. Такие посты уходят в хвост очереди,
    а не наверх. Реакции же ``None`` считаются нулём — они в знаменателе не
    стоят и рейтинг не искажают.

    ``alpha`` — обязательный keyword-аргумент, а не чтение конфига внутри:
    модуль низкоуровневый и на ``config/`` не завязан. Читает вызывающий
    (``config.classifier.get_rating_views_alpha``).
    """
    if views is None:
        return None
    engagement = (likes or 0) + (comments or 0) * 2 + (reposts or 0) * 3
    return engagement / ((int(views) + 1) ** alpha)


def vk_post_datetime(ts: Any) -> Optional[datetime]:
    """Unix-время поста ВК (поле ``date``) → наивный UTC, как времена в БД.

    Одно место на весь проект: этот разбор нужен и аудиту сбора, и фетчеру
    метрик, а две копии разошлись бы молча — ровно тот класс, из-за которого
    D-024 сводил три копии ``_call_api`` в один клиент.

    ``datetime.utcfromtimestamp`` не используем: в 3.12 она deprecated. Любое
    битое значение — ``None``, а не «сейчас»: подставленная дата обманула бы
    отсев по старости в нашу пользу.
    """
    if not ts:
        return None
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def clear_copy_history(post_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Unwrap repost copy_history to get original post.
    Migrated from old_postopus bin/utils/clear_copy_history.py

    When a post is a repost, VK stores the original in copy_history.
    This function extracts the original post data.

    Args:
        post_data: Post data from VK API

    Returns:
        Original post data (unwrapped)
    """
    if not post_data:
        return post_data

    # Check for copy_history (repost)
    copy_history = post_data.get("copy_history", [])

    if copy_history and len(copy_history) > 0:
        # Get the deepest (original) post
        original = copy_history[0]

        # Merge with current post metadata
        merged = {
            **original,
            # Keep some metadata from the repost
            "repost_from_id": post_data.get("id"),
            "repost_from_owner_id": post_data.get("owner_id"),
            "is_repost": True,
        }

        return merged

    # Not a repost
    post_data["is_repost"] = False
    return post_data


def _vk_wiki_link(url: str, link_text: str) -> str:
    """
    Кликабельная подпись во ВКонтакте (разметка постов): [url|текст]
    Символы, ломающие разметку, убираем из текста ссылки.
    """
    safe = (link_text or "").replace("[", "(").replace("]", ")").replace("|", "·").strip()
    if not safe:
        safe = "Источник"
    return f"[{url}|{safe}]"


def extract_source_attribution(post_data: Dict[str, Any], group_name: str = "") -> str:
    """
    Ссылка на исходный пост: кликабельное название источника (ВК-разметка [url|текст]).

    Args:
        post_data: VK post data
        group_name: Название сообщества или страницы (из БД / справочника)

    Returns:
        Строка вида [https://vk.com/wall...|Название сообщества]
    """
    owner_id = post_data.get("owner_id", post_data.get("from_id", 0))
    post_id = post_data.get("id", 0)

    vk_url = f"https://vk.com/wall{owner_id}_{post_id}"

    label = (group_name or "").strip() or "Источник"
    return _vk_wiki_link(vk_url, label)
