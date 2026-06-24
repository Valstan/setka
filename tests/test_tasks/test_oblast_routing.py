"""Тесты перехода области на community-mode сводки (2026-05).

Покрывают:
- чистую функцию решения «каскад vs пул» ``_use_cascade_bulletin``;
- расширенную таксономию тем (POSTOPUS_BULLETIN_THEMES + заголовки).

NB: ``_use_cascade_bulletin`` импортируется ЛЕНИВО внутри тестов, а не на уровне
модуля. Иначе collection-time импорт ``tasks.parsing_scheduler_tasks`` с настоящим
celery ломает изоляцию теста ``test_scheduler/...`` (тот подменяет celery в
sys.modules и импортирует модуль под заглушкой).
"""

from types import SimpleNamespace

import pytest

from modules.bulletin_pipeline_settings import POSTOPUS_BULLETIN_THEMES
from modules.publisher.postopus_bulletin_headers import resolve_bulletin_header

NEW_OBLAST_THEMES = [
    "proisshestviya",
    "molodezh",
    "nauka",
    "promyshlennost",
    "selhoz",
    "zdorovie",
    "zhkh",
    "priroda",
]


# ───────── _use_cascade_bulletin ─────────


def test_raion_never_cascades():
    from tasks.parsing_scheduler_tasks import _use_cascade_bulletin

    assert _use_cascade_bulletin("raion", None) is False
    assert _use_cascade_bulletin("raion", {"bulletin_mode": "communities"}) is False
    assert _use_cascade_bulletin(None, None) is False


def test_oblast_default_is_cascade():
    """Область без флага → каскад (backward-compat для tatarstan_obl/rf)."""
    from tasks.parsing_scheduler_tasks import _use_cascade_bulletin

    assert _use_cascade_bulletin("oblast", None) is True
    assert _use_cascade_bulletin("oblast", {}) is True
    assert _use_cascade_bulletin("oblast", {"bulletin_mode": "cascade"}) is True
    assert _use_cascade_bulletin("strana", None) is True


def test_oblast_communities_mode_skips_cascade():
    """bulletin_mode='communities' → область собирает из своего пула, как район."""
    from tasks.parsing_scheduler_tasks import _use_cascade_bulletin

    assert _use_cascade_bulletin("oblast", {"bulletin_mode": "communities"}) is False
    assert _use_cascade_bulletin("strana", {"bulletin_mode": "communities"}) is False


def test_non_dict_config_is_cascade():
    """Кривой config (не dict) не должен ронять — дефолт каскад для области."""
    from tasks.parsing_scheduler_tasks import _use_cascade_bulletin

    assert _use_cascade_bulletin("oblast", "garbage") is True
    assert _use_cascade_bulletin("oblast", []) is True


# ───────── таксономия ─────────


@pytest.mark.parametrize("theme", NEW_OBLAST_THEMES)
def test_new_themes_registered(theme):
    assert theme in POSTOPUS_BULLETIN_THEMES


@pytest.mark.parametrize("theme", NEW_OBLAST_THEMES)
def test_new_themes_have_human_header(theme):
    """У каждой новой темы есть человекочитаемый заголовок (не голый '📰 <theme>')."""
    region = SimpleNamespace(name="КИРОВСКАЯ ОБЛАСТЬ - ИНФО", code="kirov_obl")
    region_config = SimpleNamespace(zagolovki={}, heshteg={}, heshteg_local={})
    header = resolve_bulletin_header(region_config, theme, region)
    assert header
    assert f"📰 {theme}" not in header  # не fallback-заглушка
    assert "КИРОВСКАЯ ОБЛАСТЬ - ИНФО" in header
