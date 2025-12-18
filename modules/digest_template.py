"""
Digest template management (defaults + per-region + per-topic overrides).

Stored in Region.config (JSON) as:
{
  "digest_template": {
    "defaults": {...},
    "by_topic": {
      "Администрация": {...},
      "Культура": {...}
    }
  }
}
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional, List, Tuple

from database.connection import AsyncSessionLocal
from database.models import Region
from sqlalchemy import select


# For now we use the standard Test-Info topic list as "known topics" for UI.
# Later this can be driven by schedules / DB taxonomy.
STANDARD_TOPICS: List[str] = [
    "Администрация",
    "Культура",
    "Спорт",
    "Новости",
    "События",
    "Образование",
    "Здоровье",
    "Бизнес",
]


@dataclass(frozen=True)
class DigestTemplateSettings:
    title: str
    footer: str
    include_source_links: bool
    include_topic_hashtag: bool
    include_region_hashtags: bool
    topic_hashtag_override: str


def _default_settings() -> DigestTemplateSettings:
    # Global defaults: identical for all regions until user overrides in UI
    return DigestTemplateSettings(
        title="📋 Госпаблики сообщают:",
        footer="",
        include_source_links=True,
        include_topic_hashtag=True,
        include_region_hashtags=False,
        topic_hashtag_override="",
    )


def _coerce_bool(v: Any, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        val = v.strip().lower()
        if val in ("true", "1", "yes", "y", "on"):
            return True
        if val in ("false", "0", "no", "n", "off"):
            return False
    return default


def _coerce_str(v: Any, default: str) -> str:
    if v is None:
        return default
    if isinstance(v, str):
        return v
    return str(v)


def _merge_settings(base: DigestTemplateSettings, override: Dict[str, Any]) -> DigestTemplateSettings:
    if not override:
        return base
    return DigestTemplateSettings(
        title=_coerce_str(override.get("title"), base.title),
        footer=_coerce_str(override.get("footer"), base.footer),
        include_source_links=_coerce_bool(override.get("include_source_links"), base.include_source_links),
        include_topic_hashtag=_coerce_bool(override.get("include_topic_hashtag"), base.include_topic_hashtag),
        include_region_hashtags=_coerce_bool(override.get("include_region_hashtags"), base.include_region_hashtags),
        topic_hashtag_override=_coerce_str(override.get("topic_hashtag_override"), base.topic_hashtag_override),
    )


def parse_region_hashtags(local_hashtags: Optional[str]) -> List[str]:
    """
    Region.local_hashtags currently stored as comma-separated string (via sync script),
    but we also accept whitespace-delimited.
    """
    if not local_hashtags:
        return []
    raw = local_hashtags.replace("\n", " ").replace("\t", " ").strip()
    parts: List[str] = []
    for chunk in raw.split(","):
        parts.extend(chunk.strip().split())
    tags = [p.strip() for p in parts if p.strip()]
    # normalize: ensure leading #
    normalized = []
    for t in tags:
        normalized.append(t if t.startswith("#") else f"#{t}")
    # unique while preserving order
    seen = set()
    out = []
    for t in normalized:
        if t.lower() in seen:
            continue
        seen.add(t.lower())
        out.append(t)
    return out


def topic_to_default_hashtag(topic: str) -> str:
    """
    Default hashtag mapping by topic (used if no override configured).
    This can later be moved into a dedicated DB table.
    """
    mapping = {
        "Администрация": "#Администрация",
        "Культура": "#Культура",
        "Спорт": "#Спорт",
        "Новости": "#Новости",
        "События": "#События",
        "Образование": "#Образование",
        "Здоровье": "#Здоровье",
        "Бизнес": "#Бизнес",
    }
    tag = mapping.get(topic, "")
    if tag and not tag.startswith("#"):
        tag = f"#{tag}"
    return tag


def _get_digest_template_override(region: Region) -> Dict[str, Any]:
    cfg = region.config or {}
    if not isinstance(cfg, dict):
        return {}
    dt = cfg.get("digest_template") or {}
    return dt if isinstance(dt, dict) else {}


def compute_effective_digest_settings(
    region: Region,
    topic: str,
) -> Tuple[DigestTemplateSettings, Dict[str, Any]]:
    """
    Returns (effective_settings, raw_override_digest_template)
    """
    base = _default_settings()
    raw = _get_digest_template_override(region)
    defaults_override = raw.get("defaults") if isinstance(raw.get("defaults"), dict) else {}
    by_topic = raw.get("by_topic") if isinstance(raw.get("by_topic"), dict) else {}
    topic_override = by_topic.get(topic) if isinstance(by_topic.get(topic), dict) else {}

    merged = _merge_settings(base, defaults_override)
    merged = _merge_settings(merged, topic_override)
    return merged, raw


async def load_region_by_code(region_code: str) -> Optional[Region]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Region).where(Region.code == region_code))
        return result.scalar_one_or_none()


async def get_effective_digest_settings_for_region(
    region_code: str,
    topic: str,
) -> Optional[Dict[str, Any]]:
    """
    Convenience helper for async code: loads region and returns effective settings as dict.
    """
    region = await load_region_by_code(region_code)
    if not region:
        return None
    settings, _raw = compute_effective_digest_settings(region, topic)
    return asdict(settings)


