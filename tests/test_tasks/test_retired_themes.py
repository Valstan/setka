"""Снятые темы не имеют слотов в расписании — и это гейт, а не напоминание.

Тема снимается решением владельца и по замеру, но возвращается она одной
строкой: слот в beat-расписании выглядит безобидно, а стоит четырёх обходов
всех районов в сутки. Здесь проверяется ровно то, что снято именно ЗАПУСК.

Имена тем при этом сознательно остаются в коде: они валидные ключи
переопределений (``POSTOPUS_BULLETIN_THEMES``) и метки классификатора, а в
истории публикаций по ним лежат тысячи постов. Тест это различие и закрепляет.
"""

from __future__ import annotations

import pytest

# Тема → когда и почему снята (для сообщения об ошибке).
RETIRED_THEMES = {
    "sosed": "снята 2026-09-14: 4109 собранных постов, ноль опубликованных сводок за 30 дней",
    "addons": (
        "снята 2026-09-22: задуманному («добавка извне» — научпоп, путешествия, кино) "
        "отвечали 8 % публикаций, остальное — перелив районного контента; P180"
    ),
}


def _beat_entries_for(theme: str):
    from tasks.celery_app import app

    out = []
    for key, entry in app.conf.beat_schedule.items():
        args = entry.get("args") or ()
        if theme in tuple(args):
            out.append(key)
    return out


@pytest.mark.parametrize("theme", sorted(RETIRED_THEMES))
def test_retired_theme_has_no_beat_slots(theme):
    slots = _beat_entries_for(theme)

    assert not slots, (
        f"тема «{theme}» снова запускается по расписанию ({', '.join(sorted(slots))}); "
        f"{RETIRED_THEMES[theme]}"
    )


@pytest.mark.parametrize("theme", sorted(RETIRED_THEMES))
def test_retired_theme_name_is_still_a_valid_key(theme):
    """Снят запуск, а не имя.

    Удалить имя значило бы сломать переопределения настроек по теме и оставить
    без подписи тысячи уже опубликованных постов.
    """
    from modules.bulletin_pipeline_settings import POSTOPUS_BULLETIN_THEMES

    assert theme in POSTOPUS_BULLETIN_THEMES
