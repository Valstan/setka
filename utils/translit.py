"""Simple Cyrillic → Latin transliteration for region slug generation.

Used by `/regions/new` wizard to auto-derive `Region.code` from human-readable
center-city name (e.g. "Карачев" → "karachev"). No external dependency.
"""

from __future__ import annotations

import re

_CYR_TO_LAT: dict[str, str] = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "yo",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "kh",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify_cyrillic(text: str) -> str:
    """Convert mixed-script text to a lowercase ASCII slug.

    - Cyrillic letters → latin per `_CYR_TO_LAT`.
    - Other non-[a-z0-9] characters → single `-` separator, trimmed at edges.
    - Empty input → empty string (caller decides what to do).

    Examples:
        >>> slugify_cyrillic("Карачев")
        'karachev'
        >>> slugify_cyrillic("Малмыж, Кировская область")
        'malmyzh-kirovskaya-oblast'
        >>> slugify_cyrillic("Yoshkar-Ola 42")
        'yoshkar-ola-42'
    """
    if not text:
        return ""
    s = text.strip().lower()
    out_chars: list[str] = []
    for ch in s:
        if ch in _CYR_TO_LAT:
            out_chars.append(_CYR_TO_LAT[ch])
        else:
            out_chars.append(ch)
    out = "".join(out_chars)
    out = re.sub(r"[^a-z0-9]+", "-", out)
    return out.strip("-")
