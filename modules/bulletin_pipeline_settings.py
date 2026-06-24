"""
Настройки конвейера сводки (парсинг → сборка поста).

Хранятся в RegionConfig.bulletin_filters (JSON):
{
  "defaults": { ... },
  "by_topic": {
    "sport": { "max_post_age_hours": 48 },
    ...
  }
}
"""

from __future__ import annotations

from typing import Any, Dict, List

# Темы Celery/Postopus (для UI и переопределений)
POSTOPUS_DIGEST_THEMES: List[str] = [
    "novost",
    "kultura",
    "sport",
    "reklama",
    "admin",
    "union",
    "addons",
    "sosed",
    "detsad",
    "setka",
    "oblast",
    "neighbors",
    # Расширенная повестка для областных сообществ (community-mode oblast,
    # 2026-05). Применимы к любому региону, у которого есть communities с
    # такой category — районам не мешают (просто нет таких сообществ).
    "proisshestviya",
    "molodezh",
    "nauka",
    "promyshlennost",
    "selhoz",
    "zdorovie",
    "zhkh",
    "priroda",
]

# Значения по умолчанию, если в БД пусто
DEFAULT_PIPELINE: Dict[str, Any] = {
    "max_post_age_hours": 72.0,
    "max_posts_per_bulletin": 3,
    "min_rafinad_len_core_dedup": 50,
    "text_similarity_threshold": 0.90,
    "min_rafinad_len_similarity_dedup": 80,
    "posts_per_community_fetch": 20,
}


def _coerce_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _coerce_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def get_effective_pipeline_settings(region_config: Any, theme: str) -> Dict[str, Any]:
    """
    Сливает defaults → defaults из bulletin_filters → переопределение темы.
    Возвращает плоский словарь с числовыми ключами конвейера.
    """
    raw = getattr(region_config, "bulletin_filters", None)
    if not isinstance(raw, dict):
        raw = {}
    base_defaults = {**DEFAULT_PIPELINE, **(raw.get("defaults") or {})}
    by_topic = raw.get("by_topic") or {}
    topic_ov: Dict[str, Any] = {}
    if isinstance(by_topic, dict) and theme in by_topic and isinstance(by_topic[theme], dict):
        topic_ov = by_topic[theme]
    merged = {**base_defaults, **topic_ov}
    merged["max_post_age_hours"] = _coerce_float(merged.get("max_post_age_hours"), 72.0)
    merged["max_posts_per_bulletin"] = _coerce_int(merged.get("max_posts_per_bulletin"), 3)
    merged["min_rafinad_len_core_dedup"] = _coerce_int(merged.get("min_rafinad_len_core_dedup"), 50)
    merged["text_similarity_threshold"] = _coerce_float(
        merged.get("text_similarity_threshold"), 0.90
    )
    merged["min_rafinad_len_similarity_dedup"] = _coerce_int(
        merged.get("min_rafinad_len_similarity_dedup"), 80
    )
    merged["posts_per_community_fetch"] = _coerce_int(merged.get("posts_per_community_fetch"), 20)
    # разумные границы
    merged["max_post_age_hours"] = max(1.0, min(merged["max_post_age_hours"], 8760.0))
    merged["max_posts_per_bulletin"] = max(1, min(merged["max_posts_per_bulletin"], 10))
    merged["posts_per_community_fetch"] = max(1, min(merged["posts_per_community_fetch"], 100))
    merged["min_rafinad_len_core_dedup"] = max(10, min(merged["min_rafinad_len_core_dedup"], 500))
    merged["text_similarity_threshold"] = max(0.70, min(merged["text_similarity_threshold"], 0.99))
    merged["min_rafinad_len_similarity_dedup"] = max(
        20, min(merged["min_rafinad_len_similarity_dedup"], 1000)
    )
    return merged


def empty_bulletin_filters_template() -> Dict[str, Any]:
    """Шаблон для сохранения в БД."""
    return {
        "defaults": dict(DEFAULT_PIPELINE),
        "by_topic": {},
    }
