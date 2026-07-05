"""Tests доменного ядра классификатора (fetch_pending / record / реакции / stats)."""

from __future__ import annotations

import pytest

from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict
from tests.test_classifier.conftest import seed_post, seed_region

# ───────── fetch_pending ─────────


@pytest.mark.asyncio
async def test_pending_excludes_classified_and_wrong_status(db_session):
    await seed_region(db_session)
    await seed_post(db_session, id_=1, text="a", status="new")
    await seed_post(db_session, id_=2, text="b", status="published")  # не new/analyzed
    await seed_post(db_session, id_=3, text="c", status="analyzed")
    # у поста 3 уже есть вердикт → исключить
    await service.record_verdicts(db_session, [ClassifierVerdict(post_id=3, theme="novost")])
    out = await service.fetch_pending(db_session, limit=10)
    ids = {p["post_id"] for p in out}
    assert ids == {1}  # 2 не тот статус, 3 уже классифицирован


@pytest.mark.asyncio
async def test_pending_region_filter(db_session):
    await seed_region(db_session, id_=1, code="mi")
    await seed_region(db_session, id_=2, code="vp")
    await seed_post(db_session, id_=1, region_id=1)
    await seed_post(db_session, id_=2, region_id=2)
    out = await service.fetch_pending(db_session, region_codes=["mi"], limit=10)
    assert {p["post_id"] for p in out} == {1}
    assert out[0]["region_code"] == "mi"


# ───────── record_verdicts ─────────


@pytest.mark.asyncio
async def test_record_idempotent_and_missing(db_session):
    await seed_region(db_session)
    await seed_post(db_session, id_=1)
    first = await service.record_verdicts(
        db_session,
        [
            ClassifierVerdict(post_id=1, theme="novost", action="publish"),
            ClassifierVerdict(post_id=999, theme="x"),  # нет такого поста
        ],
    )
    assert first == {"recorded": 1, "skipped_existing": 0, "skipped_missing": 1}
    # повторно тот же пост → skip
    second = await service.record_verdicts(
        db_session, [ClassifierVerdict(post_id=1, theme="reklama")]
    )
    assert second["recorded"] == 0 and second["skipped_existing"] == 1


# ───────── реакции + agree-rate ─────────


async def _one_classification(db_session, *, theme="novost", action="publish", merge=False):
    await seed_region(db_session)
    await seed_post(db_session, id_=1)
    v = ClassifierVerdict(post_id=1, theme=theme, action=action, split=merge, confidence=70)
    await service.record_verdicts(db_session, [v])
    feed = await service.review_feed(db_session)
    return feed[0]["id"]


@pytest.mark.asyncio
async def test_agree_all_logs_applicable_types(db_session):
    cid = await _one_classification(db_session, merge=True)  # merge применим (split=True)
    out = await service.agree_all(db_session, cid)
    assert out["ok"] and set(out["agreed_types"]) == {"theme", "action", "merge"}
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["theme"]["agree"] == 1
    assert stats["by_type"]["merge"]["agree"] == 1
    assert stats["by_type"]["theme"]["agree_rate"] == 1.0


@pytest.mark.asyncio
async def test_agree_skips_merge_when_no_signal(db_session):
    cid = await _one_classification(db_session, merge=False)
    out = await service.agree_all(db_session, cid)
    assert set(out["agreed_types"]) == {"theme", "action"}
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["merge"]["total"] == 0


@pytest.mark.asyncio
async def test_correction_and_rate(db_session):
    cid = await _one_classification(db_session)
    await service.correct(db_session, cid, verdict_type="theme", operator_value="sport")
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["theme"] == {"agree": 0, "correct": 1, "total": 1, "agree_rate": 0.0}


@pytest.mark.asyncio
async def test_reaction_is_idempotent_last_wins(db_session):
    cid = await _one_classification(db_session)
    # сначала correct, потом agree по тому же типу → остаётся одна строка (agree)
    await service.correct(db_session, cid, verdict_type="action", operator_value="delete")
    await service.agree_all(db_session, cid)  # перезапишет action на agree
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["action"]["agree"] == 1
    assert stats["by_type"]["action"]["correct"] == 0


@pytest.mark.asyncio
async def test_correct_unknown_type_rejected(db_session):
    cid = await _one_classification(db_session)
    out = await service.correct(db_session, cid, verdict_type="bogus", operator_value="x")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_feed_only_unreacted(db_session):
    cid = await _one_classification(db_session)
    assert len(await service.review_feed(db_session, only_unreacted=True)) == 1
    await service.agree_all(db_session, cid)
    assert len(await service.review_feed(db_session, only_unreacted=True)) == 0
    assert len(await service.review_feed(db_session, only_unreacted=False)) == 1
