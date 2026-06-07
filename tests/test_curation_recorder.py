"""Тесты shadow-recorder LLM-курации дайджестов (PoC, миграция 035).

Покрывают: гейт (флаг + allowlist), сборку кандидатов и инвариант
«recorder НИКОГДА не валит публикацию» (изолированная сессия + best-effort).
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

# Регистрируем ОБА модуля моделей, чтобы SQLAlchemy-mapper смог резолвить
# relationship("Region") у ScheduledPublication при глобальном configure
# (инстанс любой mapped-модели его триггерит). В проде main.py грузит оба.
import database.models  # noqa: F401
import database.models_extended  # noqa: F401
from modules.curation import recorder
from modules.curation.recorder import _build_candidates, record_curation_run, should_record

# --------------------------------------------------------------------------- #
# Гейт: should_record
# --------------------------------------------------------------------------- #


def test_should_record_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DIGEST_CURATION_SHADOW_ENABLED", raising=False)
    assert should_record("mi") is False


def test_should_record_enabled_no_allowlist(monkeypatch):
    monkeypatch.setenv("DIGEST_CURATION_SHADOW_ENABLED", "1")
    monkeypatch.delenv("DIGEST_CURATION_REGION_CODES", raising=False)
    assert should_record("mi") is True
    assert should_record("ANY") is True


def test_should_record_respects_allowlist(monkeypatch):
    monkeypatch.setenv("DIGEST_CURATION_SHADOW_ENABLED", "1")
    monkeypatch.setenv("DIGEST_CURATION_REGION_CODES", "mi, ur")
    assert should_record("mi") is True
    assert should_record("MI") is True  # case-insensitive
    assert should_record("sovetsk") is False


# --------------------------------------------------------------------------- #
# Сборка кандидатов
# --------------------------------------------------------------------------- #


def test_build_candidates_maps_included_posts():
    selected = {
        "100_5": {"owner_id": -100, "id": 5, "text": "Привет район", "attachments": [{"x": 1}]},
        "100_6": {"owner_id": -100, "id": 6, "text": "Без медиа", "attachments": []},
    }
    out = _build_candidates(selected, ["100_5", "100_6"])
    assert [c["lip"] for c in out] == ["100_5", "100_6"]
    assert out[0]["has_media"] is True
    assert out[0]["url"] == "https://vk.com/wall-100_5"
    assert out[1]["has_media"] is False
    assert out[1]["text"] == "Без медиа"


def test_build_candidates_skips_missing_and_truncates():
    long_text = "я" * 5000
    selected = {"100_5": {"owner_id": -100, "id": 5, "text": long_text}}
    # posts_included ссылается на отсутствующий lip — он пропускается
    out = _build_candidates(selected, ["100_5", "999_999"])
    assert len(out) == 1
    assert len(out[0]["text"]) == recorder._MAX_CANDIDATE_TEXT


# --------------------------------------------------------------------------- #
# record_curation_run — изоляция и инвариант never-raises
# --------------------------------------------------------------------------- #


def _patch_session(monkeypatch):
    """Подменить AsyncSessionLocal на фейковый async-CM; вернуть mock-сессию."""
    session = MagicMock()
    session.add = MagicMock()
    session.commit = AsyncMock()

    class _FakeCM:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *a):
            return False

    fake_factory = MagicMock(return_value=_FakeCM())
    import database.connection as conn

    monkeypatch.setattr(conn, "AsyncSessionLocal", fake_factory)
    return session, fake_factory


@pytest.mark.asyncio
async def test_record_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("DIGEST_CURATION_SHADOW_ENABLED", raising=False)
    _, fake_factory = _patch_session(monkeypatch)
    await record_curation_run(
        region_code="mi",
        theme="novost",
        kind="regular",
        selected_by_lip={"100_5": {"owner_id": -100, "id": 5, "text": "x"}},
        posts_included=["100_5"],
        publish_result={"post_id": 7, "url": "u"},
    )
    fake_factory.assert_not_called()  # БД не тронута при выключенном флаге


@pytest.mark.asyncio
async def test_record_persists_when_enabled(monkeypatch):
    monkeypatch.setenv("DIGEST_CURATION_SHADOW_ENABLED", "1")
    monkeypatch.delenv("DIGEST_CURATION_REGION_CODES", raising=False)
    session, _ = _patch_session(monkeypatch)
    await record_curation_run(
        region_code="mi",
        theme="novost",
        kind="regular",
        selected_by_lip={"100_5": {"owner_id": -100, "id": 5, "text": "x", "attachments": [1]}},
        posts_included=["100_5"],
        publish_result={"post_id": 7, "url": "https://vk.com/wall-100_7"},
    )
    session.add.assert_called_once()
    row = session.add.call_args[0][0]
    assert row.region_code == "mi"
    assert row.theme == "novost"
    assert row.status == "pending"
    assert row.shadow is True
    assert row.total_count == 1
    assert row.published_post_id == 7
    assert row.candidates[0]["lip"] == "100_5"
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_record_noop_when_no_candidates(monkeypatch):
    monkeypatch.setenv("DIGEST_CURATION_SHADOW_ENABLED", "1")
    _, fake_factory = _patch_session(monkeypatch)
    await record_curation_run(
        region_code="mi",
        theme="novost",
        kind="regular",
        selected_by_lip={},
        posts_included=[],  # нечего парковать
        publish_result={"post_id": 7},
    )
    fake_factory.assert_not_called()


@pytest.mark.asyncio
async def test_record_never_raises_on_db_error(monkeypatch):
    monkeypatch.setenv("DIGEST_CURATION_SHADOW_ENABLED", "1")
    import database.connection as conn

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(conn, "AsyncSessionLocal", _boom)
    # Инвариант: сбой курации не пробрасывается (публикация уже прошла).
    await record_curation_run(
        region_code="mi",
        theme="novost",
        kind="regular",
        selected_by_lip={"100_5": {"owner_id": -100, "id": 5, "text": "x"}},
        posts_included=["100_5"],
        publish_result={"post_id": 7},
    )
