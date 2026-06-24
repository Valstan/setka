"""
Заголовки и хештеги сводок Postopus/SETKA (как в old_postopus Mongo).

Если в RegionConfig уже заданы zagolovki / heshteg — они имеют приоритет.
Иначе подставляются русские шаблоны по теме и названию региона.
"""

from __future__ import annotations

from typing import Any, List, Optional, Tuple

# Тематика → заголовок «Тема {регион}:» (fallback, когда в zagolovki нет ключа)
# sport — формулировка «Спортивные новости» как в запросе пользователя
_THEME_TITLE: dict[str, str] = {
    "novost": "Новости",
    "kultura": "Культура",
    "sport": "Спортивные новости",
    "reklama": "Объявления",
    "admin": "Администрация",
    "union": "Объединённая сводка",
    "addons": "Дополнительно",
    "sosed": "Новости соседей",
    "detsad": "Дошкольное образование",
    "setka": "Сетка регионов",
    "oblast": "Главное в области",
    "neighbors": "Новости соседей",
    # Расширенная областная повестка (community-mode oblast)
    "proisshestviya": "Происшествия",
    "molodezh": "Молодёжь",
    "nauka": "Наука и образование",
    "promyshlennost": "Экономика и промышленность",
    "selhoz": "Сельское хозяйство",
    "zdorovie": "Здоровье",
    "zhkh": "ЖКХ",
    "priroda": "Природа и туризм",
}

# Часть хештега по теме (кириллица, без #). К региональной части из
# heshteg_local пристыковывается в heshteg.
_THEME_HASHTAG_WORD: dict[str, str] = {
    "novost": "новости",
    "kultura": "культура",
    "sport": "спорт",
    "reklama": "реклама",
    "admin": "админ",
    "union": "объединение",
    "addons": "дополнительно",
    "sosed": "соседи",
    "detsad": "детсады",
    "setka": "сетка",
    "oblast": "область",
    "neighbors": "соседи",
    # Расширенная областная повестка (community-mode oblast)
    "proisshestviya": "происшествия",
    "molodezh": "молодёжь",
    "nauka": "наука",
    "promyshlennost": "экономика",
    "selhoz": "сельхоз",
    "zdorovie": "здоровье",
    "zhkh": "жкх",
    "priroda": "природа",
}


def region_display_name(region: Any, heshteg_local: Optional[dict]) -> str:
    """Человекочитаемое имя региона для заголовка."""
    name = getattr(region, "name", None) or ""
    name = name.strip()
    if name:
        return name
    raicentr = ""
    if heshteg_local and isinstance(heshteg_local, dict):
        raicentr = (heshteg_local.get("raicentr") or "").strip()
    if raicentr:
        return raicentr
    code = getattr(region, "code", None) or ""
    return str(code) or "Регион"


def resolve_bulletin_header(region_config: Any, theme: str, region: Any) -> str:
    """
    Заголовок сводки: из zagolovki[theme], иначе русский шаблон по теме и региону.
    """
    z = getattr(region_config, "zagolovki", None) or {}
    if isinstance(z, dict) and theme in z:
        raw = z.get(theme)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    hl = getattr(region_config, "heshteg_local", None) or {}
    if not isinstance(hl, dict):
        hl = {}
    rlabel = region_display_name(region, hl)
    prefix = _THEME_TITLE.get(theme)
    if prefix:
        return f"{prefix} {rlabel}:"
    return f"📰 {theme} {rlabel}:"


def resolve_bulletin_hashtags(
    region_config: Any,
    theme: str,
) -> Tuple[List[str], str]:
    """
    Возвращает (список тематических тегов без #, один или несколько) и локальный
    тег региона с # или "".

    Как в Mongo: combined «спортЛебяжье» в heshteg[theme] и отдельно #лебяжье из raicentr.
    """
    heshteg = getattr(region_config, "heshteg", None) or {}
    heshteg_local = getattr(region_config, "heshteg_local", None) or {}
    if not isinstance(heshteg, dict):
        heshteg = {}
    if not isinstance(heshteg_local, dict):
        heshteg_local = {}

    tags: List[str] = []
    if theme in heshteg and str(heshteg[theme]).strip():
        tags.append(str(heshteg[theme]).strip().lstrip("#"))
    else:
        raicentr = (heshteg_local.get("raicentr") or "").strip().lstrip("#")
        tw = _THEME_HASHTAG_WORD.get(theme, theme)
        if raicentr:
            tags.append(f"{tw}{raicentr}")
        else:
            tags.append(tw)

    raicentr_raw = (heshteg_local.get("raicentr") or "").strip()
    local_hashtag = f"#{raicentr_raw.lstrip('#')}" if raicentr_raw else ""

    return tags, local_hashtag


def resolve_mourning_bulletin_format() -> Tuple[str, List[str], str]:
    """
    Mourning-bulletin must be published without any header and hashtags.
    """
    return "", [], ""
