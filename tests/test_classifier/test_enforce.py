"""Тесты enforce-слоя классификатора (modules/classifier/enforce.py).

Вердикт delete/hold выключает пост из публикации; правка оператора
(verdict_type='action', outcome='correct') главнее вердикта ИИ; выключенный
enforcement / чужой регион / сбой БД → пустой блок-набор (fail-open).
"""

from __future__ import annotations

import pytest

from database.models_extended import ClassificationCorrection, ContentClassification
from modules.classifier.enforce import fetch_blocked_lips


def _cls(lip, action, region_code="mi"):
    return ContentClassification(
        lip=lip,
        region_code=region_code,
        post_text="t",
        post_url="u",
        source="routine",
        verdict={"theme": "тема", "action": action},
        shadow=True,
    )


async def _seed(session, rows):
    for r in rows:
        session.add(r)
    await session.commit()


@pytest.mark.asyncio
async def test_disabled_by_default(db_session, monkeypatch):
    monkeypatch.delenv("CLASSIFIER_ENFORCE_ENABLED", raising=False)
    await _seed(db_session, [_cls("1_1", "delete")])
    assert await fetch_blocked_lips(db_session, "mi") == set()


@pytest.mark.asyncio
async def test_blocks_delete_and_hold_not_publish(db_session, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    monkeypatch.delenv("CLASSIFIER_ENFORCE_REGION_CODES", raising=False)
    await _seed(
        db_session,
        [_cls("1_1", "delete"), _cls("1_2", "hold"), _cls("1_3", "publish")],
    )
    assert await fetch_blocked_lips(db_session, "mi") == {"1_1", "1_2"}


@pytest.mark.asyncio
async def test_operator_correction_overrides_ai(db_session, monkeypatch):
    """Оператор перевёл delete→publish — пост разблокирован; publish→delete — заблокирован."""
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    unblocked = _cls("2_1", "delete")
    blocked = _cls("2_2", "publish")
    await _seed(db_session, [unblocked, blocked])
    await _seed(
        db_session,
        [
            ClassificationCorrection(
                classification_id=unblocked.id,
                lip=unblocked.lip,
                verdict_type="action",
                outcome="correct",
                ai_value="delete",
                operator_value="publish",
            ),
            ClassificationCorrection(
                classification_id=blocked.id,
                lip=blocked.lip,
                verdict_type="action",
                outcome="correct",
                ai_value="publish",
                operator_value="delete",
            ),
        ],
    )
    assert await fetch_blocked_lips(db_session, "mi") == {"2_2"}


@pytest.mark.asyncio
async def test_agree_reaction_keeps_ai_action(db_session, monkeypatch):
    """outcome='agree' — не правка: ИИ-действие остаётся эффективным."""
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    cls = _cls("3_1", "delete")
    await _seed(db_session, [cls])
    await _seed(
        db_session,
        [
            ClassificationCorrection(
                classification_id=cls.id,
                lip=cls.lip,
                verdict_type="action",
                outcome="agree",
                ai_value="delete",
            )
        ],
    )
    assert await fetch_blocked_lips(db_session, "mi") == {"3_1"}


@pytest.mark.asyncio
async def test_region_allowlist_gates_target_region(db_session, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    monkeypatch.setenv("CLASSIFIER_ENFORCE_REGION_CODES", "mi,ur")
    await _seed(db_session, [_cls("4_1", "delete")])
    assert await fetch_blocked_lips(db_session, "mi") == {"4_1"}
    assert await fetch_blocked_lips(db_session, "vp") == set()


@pytest.mark.asyncio
async def test_empty_allowlist_means_all_regions(db_session, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    monkeypatch.setenv("CLASSIFIER_ENFORCE_REGION_CODES", "")
    await _seed(db_session, [_cls("5_1", "hold", region_code="mi")])
    # Пустой allowlist = enforce включён во всех регионах, но вердикт
    # применяется только к региону, в контексте которого вынесен
    # (гео-относительные правила: «чужой район» mi ≠ «чужой район» vp).
    assert await fetch_blocked_lips(db_session, "mi") == {"5_1"}
    assert await fetch_blocked_lips(db_session, "vp") == set()


@pytest.mark.asyncio
async def test_verdict_scoped_to_own_region(db_session, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")
    monkeypatch.setenv("CLASSIFIER_ENFORCE_REGION_CODES", "")
    await _seed(
        db_session,
        [
            _cls("6_1", "delete", region_code="mi"),
            _cls("6_2", "delete", region_code="nolinsk"),
        ],
    )
    assert await fetch_blocked_lips(db_session, "mi") == {"6_1"}
    assert await fetch_blocked_lips(db_session, "nolinsk") == {"6_2"}


@pytest.mark.asyncio
async def test_db_failure_fails_open(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_ENFORCE_ENABLED", "1")

    class _BoomSession:
        async def execute(self, *a, **kw):
            raise RuntimeError("db down")

    assert await fetch_blocked_lips(_BoomSession(), "mi") == set()
