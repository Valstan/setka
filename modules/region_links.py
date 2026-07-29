"""Публичный список VK-сообществ сети — «куда выходят сводки».

Заказ владельца 2026-07-29: нужен компактный список всех региональных групп,
куда SETKA публикует сводки, в виде, который можно **целиком скопировать и
отправить одним сообщением/постом в VK**. Формат строки — «Малмыж ИНФО —
https://vk.com/club158787639»: имя коротким (город + ИНФО), ссылка отдельным
токеном, чтобы VK сам сделал её кликабельной. Список разбит на области;
областная группа идёт первой строкой своего блока.

Здесь только чистые функции форматирования (без БД и FastAPI) — их зовёт
``web/api/regions.py`` (эндпоинт ``GET /api/regions/vk-links``) и они же
покрыты юнит-тестами.

Инварианты данных, на которых стоит модуль (см. ``database/models.py``):

* ``Region.vk_group_id`` — **отрицательный** owner_id группы VK; ссылка
  строится как ``https://vk.com/club{abs(id)}`` (канон всего проекта, ср.
  ``web/templates/regions.html``). Исторически один регион (tuzha) лежал в БД
  положительным, поэтому ``abs`` обязателен.
* ``Region.name`` хранится в формате «МАЛМЫЖ - ИНФО» (капсом), но у части
  записей — свободный «Тужа, Кировская область». Обе формы приводим к
  «Малмыж ИНФО» / «Тужа ИНФО».
* Регион без ``vk_group_id`` — заготовка (район заведён, группа ещё не
  создана): в список не попадает.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

# Суффикс, который носят все группы сети. В БД он в имени («МАЛМЫЖ - ИНФО»),
# в списке для людей — тоже, но одним видом: «Малмыж ИНФО».
INFO_SUFFIX = "ИНФО"

# Нарицательные гео-слова: в Title Case остаются строчными, иначе «КИРОВСКАЯ
# ОБЛАСТЬ» превращается в «Кировская Область». Имена собственные из двух слов
# («Вятские Поляны», «Белая Холуница») при этом капитализируются целиком.
_LOWERCASE_WORDS = frozenset(
    {
        "область",
        "обл",
        "край",
        "республика",
        "район",
        "округ",
        "городской",
        "муниципальный",
        "и",
    }
)

# Тире всех видов, которыми отделён суффикс «- ИНФО» в именах регионов.
_DASHES = ("-", "–", "—")

# Разделитель «имя — ссылка» в строке списка.
_ITEM_SEPARATOR = " — "

# Заголовок блока для групп, у которых нет родительской области.
OTHER_BLOCK_TITLE = "Другие"
OTHER_BLOCK_CODE = "_other"


def community_url(vk_group_id: int) -> str:
    """Ссылка на группу VK по её owner_id (знак не важен)."""
    return f"https://vk.com/club{abs(int(vk_group_id))}"


def _titlecase_ru(text: str) -> str:
    """«КИРОВСКАЯ ОБЛАСТЬ» → «Кировская область», «ВЯТСКИЕ ПОЛЯНЫ» → «Вятские Поляны».

    Применяется **только** к строкам целиком в верхнем регистре: имена,
    заведённые человеком в смешанном регистре («Тужа, Кировская область»),
    трогать нельзя — там регистр уже осмысленный.
    """
    if not text or text != text.upper():
        return text
    words: List[str] = []
    for idx, word in enumerate(text.split()):
        low = word.lower()
        if idx > 0 and low.strip(".,") in _LOWERCASE_WORDS:
            words.append(low)
            continue
        # Дефисные составные («КИРОВО-ЧЕПЕЦК») капитализируем по частям.
        words.append("-".join(part.capitalize() for part in low.split("-")))
    return " ".join(words)


def base_title(name: str, center_city: Optional[str] = None) -> str:
    """Человеческое название региона без суффикса «ИНФО».

    «МАЛМЫЖ - ИНФО» → «Малмыж»; «Тужа, Кировская область» → «Тужа»;
    «КИРОВСКАЯ ОБЛАСТЬ - ИНФО» → «Кировская область».
    """
    raw = (name or "").strip()
    if not raw:
        return (center_city or "").split(",")[0].strip()
    head = raw.split(",", 1)[0].strip()  # отрезаем «, Кировская область»
    for dash in _DASHES:
        marker = f"{dash} {INFO_SUFFIX}"
        idx = head.upper().rfind(marker)
        if idx != -1:
            head = head[:idx].strip()
            break
    else:
        # Имя без тире, но с хвостом «ИНФО» («МАЛМЫЖ ИНФО») — тоже отрезаем.
        if head.upper().endswith(INFO_SUFFIX):
            head = head[: -len(INFO_SUFFIX)].strip(" -–—")
    return _titlecase_ru(head) or _titlecase_ru(raw)


def short_name(name: str, center_city: Optional[str] = None) -> str:
    """Короткое имя сообщества для списка: «Малмыж ИНФО»."""
    title = base_title(name, center_city)
    return f"{title} {INFO_SUFFIX}".strip()


def _sort_key(value: str) -> str:
    """Ключ сортировки по-русски: «ё» не должна уезжать в конец после «я»."""
    return value.lower().replace("ё", "е")


def _item(region: Any) -> Dict[str, Any]:
    return {
        "code": region.code,
        "kind": getattr(region, "kind", "raion"),
        "name": short_name(region.name, getattr(region, "center_city", None)),
        "url": community_url(region.vk_group_id),
    }


def build_blocks(regions: Sequence[Any]) -> List[Dict[str, Any]]:
    """Сгруппировать регионы по областям в блоки списка.

    На входе — ORM-объекты ``Region`` (нужны поля ``code``, ``name``, ``kind``,
    ``vk_group_id``, ``parent_region_id``, ``is_active``). Возвращаются только
    блоки с непустым списком: у области без единой готовой группы показывать
    нечего.

    Порядок: блоки по названию области, внутри блока — сама область первой
    строкой, затем районы по алфавиту. Районы, чья область неизвестна или сама
    неактивна, собираются в блок «Другие», который идёт последним.
    """
    usable = [
        r
        for r in regions
        if getattr(r, "is_active", False) and getattr(r, "vk_group_id", None) is not None
    ]
    oblasts = {r.id: r for r in usable if getattr(r, "kind", "raion") == "oblast"}

    children: Dict[Any, List[Any]] = {}
    orphans: List[Any] = []
    for region in usable:
        if getattr(region, "kind", "raion") == "oblast":
            continue
        parent_id = getattr(region, "parent_region_id", None)
        if parent_id in oblasts:
            children.setdefault(parent_id, []).append(region)
        else:
            orphans.append(region)

    blocks: List[Dict[str, Any]] = []
    for oblast in sorted(oblasts.values(), key=lambda r: _sort_key(base_title(r.name))):
        items = [_item(oblast)]
        items += [
            _item(r)
            for r in sorted(
                children.get(oblast.id, []),
                key=lambda r: _sort_key(short_name(r.name, getattr(r, "center_city", None))),
            )
        ]
        blocks.append(
            {
                "code": oblast.code,
                "title": base_title(oblast.name),
                "items": items,
            }
        )

    if orphans:
        blocks.append(
            {
                "code": OTHER_BLOCK_CODE,
                "title": OTHER_BLOCK_TITLE,
                "items": [
                    _item(r)
                    for r in sorted(
                        orphans,
                        key=lambda r: _sort_key(
                            short_name(r.name, getattr(r, "center_city", None))
                        ),
                    )
                ],
            }
        )
    return blocks


def render_block_text(block: Dict[str, Any]) -> str:
    """Текст одного блока: заголовок области + строки «Имя — ссылка»."""
    lines = [block["title"]]
    lines += [f"{item['name']}{_ITEM_SEPARATOR}{item['url']}" for item in block["items"]]
    return "\n".join(lines)


def render_text(blocks: Iterable[Dict[str, Any]]) -> str:
    """Полный текст списка — то, что уходит в буфер обмена и дальше в VK.

    Блоки разделены пустой строкой; ссылки — отдельными токенами, VK делает их
    кликабельными сам. Без markdown и эмодзи: в VK они не форматируются, а
    занимают место.
    """
    return "\n\n".join(render_block_text(block) for block in blocks)
