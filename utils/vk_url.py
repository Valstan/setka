"""Parse a VK community link to (group_id, screen_name).

Accepts what users typically paste into the «Главная группа региона» field of
the region-creation wizard:

- ``https://vk.com/club12345``  → ``(12345, None)``
- ``https://vk.com/public12345`` → ``(12345, None)``
- ``https://vk.com/screen_name`` → ``(None, "screen_name")`` (caller resolves)
- ``vk.com/club42`` (без схемы) — допустимо
- ``-12345`` / ``12345`` (чистый ID) → ``(12345, None)``
- невалид/мусор → ``(None, None)``

`group_id` возвращается всегда **положительным** (как в `Region.vk_group_id`).
Caller сам решает, нужно ли уйти в `-group_id` для VK API (`wall.get` etc.).
"""

from __future__ import annotations

import re
from typing import Optional, Tuple

_CLUB_PUBLIC_RE = re.compile(r"^(?:club|public)(\d+)$", re.IGNORECASE)
_SCREEN_RE = re.compile(r"^[a-zA-Z0-9_.]{3,32}$")
_NUMERIC_RE = re.compile(r"^-?\d+$")


def parse_vk_group_url(url: str) -> Tuple[Optional[int], Optional[str]]:
    """Return ``(group_id, screen_name)`` parsed from a VK community link.

    Either one of the two is set; on success the other is ``None``. On failure
    both are ``None``.
    """
    if not url:
        return (None, None)
    s = url.strip()
    if not s:
        return (None, None)

    # Чистый числовой ID — допустим.
    if _NUMERIC_RE.match(s):
        gid = abs(int(s))
        return (gid if gid > 0 else None, None)

    # Снимаем схему и хост vk.com (или m.vk.com).
    s = re.sub(r"^https?://", "", s, flags=re.IGNORECASE)
    s = re.sub(r"^(?:m\.|www\.)?vk\.(?:com|ru)/", "", s, flags=re.IGNORECASE)

    # Хвост после первого '/', '?', '#' — обрезаем.
    s = re.split(r"[/?#]", s, maxsplit=1)[0].strip()
    if not s:
        return (None, None)

    m = _CLUB_PUBLIC_RE.match(s)
    if m:
        gid = int(m.group(1))
        return (gid if gid > 0 else None, None)

    if _SCREEN_RE.match(s):
        return (None, s.lower())

    return (None, None)
