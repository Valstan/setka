"""Тесты перехода области на community-mode дайджесты (2026-05).

Покрывают:
- чистую функцию решения «каскад vs пул» ``_use_cascade_digest``;
- расширенную таксономию тем (POSTOPUS_DIGEST_THEMES + заголовки).

NB: ``_use_cascade_digest`` импортируется ЛЕНИВО внутри тестов, а не на уровне
модуля. Иначе collection-time импорт ``tasks.parsing_scheduler_tasks`` с настоящим
celery ломает изоляцию теста ``test_scheduler/...`` (тот подменяет celery в
sys.modules и импортирует модуль под заглушкой).
"""

from types import SimpleNamespace

import pytest

from modules.digest_pipeline_settings import POSTOPUS_DIGEST_THEMES
from modules.publisher.postopus_digest_headers import resolve_digest_header

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


# ───────── _use_cascade_digest ─────────


def test_raion_never_cascades():
    from tasks.parsing_scheduler_tasks import _use_cascade_digest

    assert _use_cascade_digest("raion", None) is False
    assert _use_cascade_digest("raion", {"digest_mode": "communities"}) is False
    assert _use_cascade_digest(None, None) is False


def test_oblast_default_is_cascade():
    """Область без флага → каскад (backward-compat для tatarstan_obl/rf)."""
    from tasks.parsing_scheduler_tasks import _use_cascade_digest

    assert _use_cascade_digest("oblast", None) is True
    assert _use_cascade_digest("oblast", {}) is True
    assert _use_cascade_digest("oblast", {"digest_mode": "cascade"}) is True
    assert _use_cascade_digest("strana", None) is True


def test_oblast_communities_mode_skips_cascade():
    """digest_mode='communities' → область собирает из своего пула, как район."""
    from tasks.parsing_scheduler_tasks import _use_cascade_digest

    assert _use_cascade_digest("oblast", {"digest_mode": "communities"}) is False
    assert _use_cascade_digest("strana", {"digest_mode": "communities"}) is False


def test_non_dict_config_is_cascade():
    """Кривой config (не dict) не должен ронять — дефолт каскад для области."""
    from tasks.parsing_scheduler_tasks import _use_cascade_digest

    assert _use_cascade_digest("oblast", "garbage") is True
    assert _use_cascade_digest("oblast", []) is True


# ───────── таксономия ─────────


@pytest.mark.parametrize("theme", NEW_OBLAST_THEMES)
def test_new_themes_registered(theme):
    assert theme in POSTOPUS_DIGEST_THEMES


@pytest.mark.parametrize("theme", NEW_OBLAST_THEMES)
def test_new_themes_have_human_header(theme):
    """У каждой новой темы есть человекочитаемый заголовок (не голый '📰 <theme>')."""
    region = SimpleNamespace(name="КИРОВСКАЯ ОБЛАСТЬ - ИНФО", code="kirov_obl")
    region_config = SimpleNamespace(zagolovki={}, heshteg={}, heshteg_local={})
    header = resolve_digest_header(region_config, theme, region)
    assert header
    assert f"📰 {theme}" not in header  # не fallback-заглушка
    assert "КИРОВСКАЯ ОБЛАСТЬ - ИНФО" in header
