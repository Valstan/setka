"""
Извлечение ссылок на посты ВК (wall) из текста сводок и постов.
"""

from __future__ import annotations

import re
from typing import List, Set, Tuple

# Полные URL и сокращённые wall-123_456
_WALL_PATTERNS = [
    re.compile(
        r"(?:https?://)?(?:m\.)?vk\.com/wall(-?\d+)_(\d+)",
        re.IGNORECASE,
    ),
    re.compile(r"\bwall(-?\d+)_(\d+)\b", re.IGNORECASE),
]


def extract_wall_post_refs_from_text(text: str) -> List[Tuple[int, int]]:
    """
    Возвращает список (owner_id, post_id) в порядке появления, без дубликатов.
    """
    if not text:
        return []
    seen: Set[Tuple[int, int]] = set()
    out: List[Tuple[int, int]] = []
    for pat in _WALL_PATTERNS:
        for m in pat.finditer(text):
            try:
                oid = int(m.group(1))
                pid = int(m.group(2))
            except (ValueError, TypeError):
                continue
            key = (oid, pid)
            if key in seen:
                continue
            seen.add(key)
            out.append(key)
    return out
