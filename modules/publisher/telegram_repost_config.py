"""
Runtime config for Telegram repost flows (env-tunable, safe defaults).

Mirrors the style of ``modules.copy_setka_network`` env helpers: behavior is
tweakable on prod via ``/etc/setka/setka.env`` without code edits. Secrets
(bot tokens) live ONLY in env as ``TELEGRAM_TOKEN_<NAME>`` (pool #008) — here
we deal only with non-secret toggles/limits and per-channel hashtags.
"""

from __future__ import annotations

import os
from typing import List


def _getenv(name: str, default: str = "") -> str:
    return os.getenv(name, default)


def telegram_repost_disabled() -> bool:
    """Global kill-switch for ALL Telegram repost flows (A and B)."""
    return _getenv("TELEGRAM_REPOST_DISABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def get_gonba_community_id() -> int:
    """DB id of the «Гоньба - жемчужина Вятки» community to mirror (Flow B)."""
    try:
        return int(_getenv("GONBA_COMMUNITY_ID", "847"))
    except ValueError:
        return 847


def get_gonba_max_posts_per_run() -> int:
    """Cap of new wall posts mirrored per Flow-B run (anti-flood)."""
    try:
        return max(1, int(_getenv("GONBA_MAX_POSTS_PER_RUN", "3")))
    except ValueError:
        return 3


def get_gonba_max_post_age_hours() -> float:
    """Skip wall posts older than this (avoids back-fill spam on cold start)."""
    try:
        return float(_getenv("GONBA_MAX_POST_AGE_HOURS", "48"))
    except ValueError:
        return 48.0


def get_telegram_extra_hashtags(channel: str) -> List[str]:
    """
    Optional Telegram-tailored hashtags appended to a repost for a channel.

    Owner: hashtags are mostly unwanted in TG, but a small TG-tailored tag is OK.
    Off by default. Configure via env ``TELEGRAM_EXTRA_HASHTAGS_<CHAN>`` where
    ``<CHAN>`` is the channel username upper-cased without ``@`` (e.g.
    ``TELEGRAM_EXTRA_HASHTAGS_MALMYZH_INFO="Малмыж"``). Comma/space separated.
    """
    if not channel:
        return []
    key = "TELEGRAM_EXTRA_HASHTAGS_" + channel.lstrip("@").upper()
    raw = _getenv(key, "").strip()
    if not raw:
        return []
    parts = [p.strip().lstrip("#") for chunk in raw.split(",") for p in chunk.split()]
    return [p for p in parts if p]
