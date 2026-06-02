"""Рендер ответа-оффера из тела ``MessageTemplate``.

Подставляет ``{author_name}``, ``{community_name}``, ``{region_name}``; терпим к
неизвестным/отсутствующим плейсхолдерам. Соблюдает лимит длины ЛС VK (2048).
Если имя автора не резолвится — убирает артефакты вида «Здравствуйте, !».
"""

from __future__ import annotations

from typing import Optional

# VK direct message: не более 2048 символов (с эмодзи).
VK_MESSAGE_MAX_LEN = 2048


class _SafeDict(dict):
    """dict, отдающий '' для отсутствующих ключей — чтобы format_map не падал."""

    def __missing__(self, key):  # noqa: D401
        return ""


def render(
    template_body: str,
    *,
    author_name: Optional[str] = None,
    community_name: Optional[str] = None,
    region_name: Optional[str] = None,
) -> str:
    """Подставить значения в тело шаблона и обрезать до лимита VK."""
    body = template_body or ""
    name = (author_name or "").strip()
    values = _SafeDict(
        author_name=name,
        community_name=(community_name or "").strip(),
        region_name=(region_name or "").strip(),
    )

    try:
        text = body.format_map(values)
    except (ValueError, IndexError, KeyError):
        # Кривой плейсхолдер в шаблоне (например, одиночная '{') — отдаём как есть.
        text = body

    if not name:
        # Подчищаем «висящую» запятую от пропущенного имени.
        for bad in (", !", ",!", " ,", " !"):
            text = text.replace(bad, "!" if "!" in bad else "")
        while "  " in text:
            text = text.replace("  ", " ")

    text = text.strip()
    if len(text) > VK_MESSAGE_MAX_LEN:
        text = text[: VK_MESSAGE_MAX_LEN - 1].rstrip() + "…"
    return text
