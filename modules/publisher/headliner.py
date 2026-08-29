"""Хедлайнер волны: сильнейший пост отдельной короткой публикацией.

Этап 3 ребрендинга (план 2026-08-29). Обоснование — прямой замер на своей
сети (``docs/ops/vk-findability-playbook.md``): 26.08 один и тот же некролог
вышел у Кирса двумя форматами в один час — короткий одиночный пост (653 знака,
2 фото) собрал **436 966 просмотров** через рекомендации, он же внутри
сводки-простыни — **167**. Рекомендации ВК берут короткие одиночные посты и
не берут сводки; порог подписчиков снят официально (пресс-релиз 28.05.2026).

Механика: перед сборкой сводки из отобранных постов выделяется один
«хедлайнер» — лучший по тому же рейтингу, которым сводка сортирует посты
(``post_rating``), с текстом «одиночного» размера. Он публикуется отдельным
постом БЕЗ шапки-заголовка (шапка «Новости …:» — маркер сводки; хедлайнер
должен выглядеть как обычный пост), с атрибуцией источника и локальным
хэштегом. Остальные посты идут сводкой как раньше.

Правила консервативности:
- не больше одного хедлайнера на волну;
- пул меньше ``MIN_POOL`` постов — без хедлайнера (сводка и так короткая);
- текст кандидата в ``MIN_LEN..MAX_LEN`` — простыня хедлайнером не станет;
- откат per-region: ``regions.config['headliner'] = false`` (дефолт включено).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from utils.vk_attachments import build_attachments_list, extract_vk_attachments

logger = logging.getLogger(__name__)

# Пул меньше трёх — сводка сама короткая, дробить нечего.
MIN_POOL = 3
# Диапазон «одиночного» текста: короче — обычно обрывок объявления,
# длиннее — та же простыня, ради ухода от которой всё затевалось.
MIN_LEN = 80
MAX_LEN = 900


def headliner_enabled(region_config_json: Optional[dict]) -> bool:
    """Флаг отката: ``regions.config['headliner']`` (дефолт — включено)."""
    if isinstance(region_config_json, dict):
        return bool(region_config_json.get("headliner", True))
    return True


def _rating(post: Dict[str, Any]) -> Optional[float]:
    from config.classifier import get_rating_views_alpha
    from utils.post_utils import post_rating

    def _count(field: str):
        value = post.get(field)
        if isinstance(value, dict):
            return value.get("count")
        return value

    return post_rating(
        views=_count("views"),
        likes=_count("likes"),
        comments=_count("comments"),
        reposts=_count("reposts"),
        alpha=get_rating_views_alpha(),
    )


def pick_headliner(posts: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Лучший по рейтингу пост «одиночного» формата, либо ``None``.

    Кандидат обязан иметь текст в диапазоне и измеренный рейтинг (пост без
    ``views`` в сводке и так уезжает в хвост — хедлайнером ему не быть).
    """
    if len(posts) < MIN_POOL:
        return None
    best: Tuple[float, Optional[Dict[str, Any]]] = (-1.0, None)
    for post in posts:
        text = (post.get("text") or "").strip()
        if not (MIN_LEN <= len(text) <= MAX_LEN):
            continue
        score = _rating(post)
        if score is None:
            continue
        if score > best[0]:
            best = (score, post)
    return best[1]


def build_headliner(
    post: Dict[str, Any],
    *,
    group_name: str = "",
    local_hashtag: str = "",
) -> Tuple[str, List[str]]:
    """Текст и вложения одиночного поста: текст + атрибуция + локальный тег."""
    from utils.post_utils import extract_source_attribution

    parts = [(post.get("text") or "").strip()]
    attribution = ""
    if not post.get("hide_attribution"):
        attribution = extract_source_attribution(post, group_name)
        if attribution:
            parts.extend(["", attribution])
    if local_hashtag:
        parts.extend(["", local_hashtag])
    attachments = build_attachments_list(extract_vk_attachments(post))
    return "\n".join(parts), attachments
