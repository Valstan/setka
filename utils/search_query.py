"""Серверная часть универсального tiered-поиска (brain pool #035).

Нормализация запроса, токенизация (многотокен AND), компактная форма
«номерных» токенов (``240-1`` ≡ ``2401``) и автокоррекция раскладки RU↔EN
для повторного запроса при нуле результатов. Клиентское зеркало —
``web/static/js/search_match.js`` (там же subsequence/fuzzy для списков
в памяти; на сервере fuzzy делает Postgres ``pg_trgm``).
"""

from __future__ import annotations

import re

_SEPARATORS_RE = re.compile(r"[\s\-./]")
_SPACES_RE = re.compile(r"\s+")

_EN_ROW = "qwertyuiop[]asdfghjkl;'zxcvbnm,.`"
_RU_ROW = "йцукенгшщзхъфывапролджэячсмитьбюё"
_EN2RU = dict(zip(_EN_ROW, _RU_ROW))
_RU2EN = dict(zip(_RU_ROW, _EN_ROW))


def normalize_query(q: str) -> str:
    """lower + ``ё→е`` + схлопывание пробелов — всегда, до любого сравнения."""
    return _SPACES_RE.sub(" ", (q or "").lower().replace("ё", "е")).strip()


def tokenize(q: str) -> list[str]:
    """Токены для многотокен-AND (каждый должен совпасть в любом месте)."""
    return [t for t in normalize_query(q).split(" ") if t]


def compact_number(token: str) -> str | None:
    """Компактная форма «преимущественно цифрового» токена (без ``-./`` и пробелов).

    ``240-1`` → ``2401``; для нечисловых токенов — ``None``.
    """
    core = _SEPARATORS_RE.sub("", token or "")
    if not core:
        return None
    digits = sum(ch.isdigit() for ch in core)
    if digits < 2 or digits * 2 < len(core):
        return None
    return core


def convert_layout(q: str) -> str:
    """Конвертация из «не той» раскладки (``ldbufntkm`` → ``двигатель``).

    Направление — по преобладающему алфавиту строки. Если конвертация ничего
    не меняет, возвращается вход как есть.
    """
    s = (q or "").lower()
    latin = sum(1 for ch in s if "a" <= ch <= "z")
    cyr = sum(1 for ch in s if "а" <= ch <= "я" or ch == "ё")
    mapping = _EN2RU if latin >= cyr else _RU2EN
    return "".join(mapping.get(ch, ch) for ch in s)


def query_variants(q: str) -> list[list[str]]:
    """Варианты токен-наборов по убыванию точности: исходный → другая раскладка.

    Следующий вариант пробуется, только если предыдущий дал ноль результатов.
    """
    base = normalize_query(q)
    variants = [tokenize(base)] if base else []
    alt = convert_layout(base)
    if alt and alt != base:
        variants.append(tokenize(alt))
    return [v for v in variants if v]
