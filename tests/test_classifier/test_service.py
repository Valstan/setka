"""Tests доменного ядра классификатора (источник = свод­ки, ключ = lip)."""

from __future__ import annotations

import pytest

from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict
from tests.test_classifier.conftest import _cand, seed_run

# ───────── fetch_pending ─────────


@pytest.mark.asyncio
async def test_pending_flattens_dedups_excludes_classified(db_session):
    # две свод­ки, пост L1 в обеих (дедуп), плюс L2, L3
    await seed_run(db_session, candidates=[_cand("1_10"), _cand("1_20")], days_ago=0)
    await seed_run(db_session, candidates=[_cand("1_10"), _cand("1_30")], days_ago=1)
    # L2 уже классифицирован → исключить
    await service.record_verdicts(
        db_session, [ClassifierVerdict(lip="1_20", theme="t", region_code="mi", text="x")]
    )
    out = await service.fetch_pending(db_session, limit=10)
    lips = {p["lip"] for p in out}
    assert lips == {"1_10", "1_30"}  # 1_20 классифицирован, 1_10 не задублирован


@pytest.mark.asyncio
async def test_pending_region_filter(db_session):
    await seed_run(db_session, region_code="mi", candidates=[_cand("1_10")])
    await seed_run(db_session, region_code="vp", candidates=[_cand("2_10")])
    out = await service.fetch_pending(db_session, region_codes=["mi"], limit=10)
    assert {p["lip"] for p in out} == {"1_10"}
    assert out[0]["region_code"] == "mi"


@pytest.mark.asyncio
async def test_pending_window_excludes_old(db_session):
    await seed_run(db_session, candidates=[_cand("1_10")], days_ago=30)  # вне окна (7 дней)
    out = await service.fetch_pending(db_session, limit=10, days=7)
    assert out == []


# ───────── record_verdicts ─────────


@pytest.mark.asyncio
async def test_record_uses_echo_snapshot(db_session):
    out = await service.record_verdicts(
        db_session,
        [
            ClassifierVerdict(
                lip="1_10",
                theme="novost",
                action="publish",
                region_code="mi",
                text="дождь",
                url="https://vk.com/x",
            )
        ],
    )
    assert out == {"recorded": 1, "skipped_existing": 0, "skipped_missing": 0}
    feed = await service.review_feed(db_session)
    assert feed[0]["lip"] == "1_10"
    assert feed[0]["post_text"] == "дождь"
    assert feed[0]["region_code"] == "mi"


@pytest.mark.asyncio
async def test_record_falls_back_to_svodka_snapshot(db_session):
    # вердикт без эха региона/текста → добираем из свод­ки
    await seed_run(db_session, region_code="mi", candidates=[_cand("1_10", text="из свод­ки")])
    out = await service.record_verdicts(
        db_session, [ClassifierVerdict(lip="1_10", theme="t")], region_codes_fallback=["mi"]
    )
    assert out["recorded"] == 1
    feed = await service.review_feed(db_session)
    assert feed[0]["region_code"] == "mi" and feed[0]["post_text"] == "из свод­ки"


@pytest.mark.asyncio
async def test_record_skips_when_no_region(db_session):
    # нет эха региона и нет в свод­ках → пропуск (region NOT NULL)
    out = await service.record_verdicts(db_session, [ClassifierVerdict(lip="9_99", theme="t")])
    assert out == {"recorded": 0, "skipped_existing": 0, "skipped_missing": 1}


@pytest.mark.asyncio
async def test_record_idempotent(db_session):
    v = ClassifierVerdict(lip="1_10", theme="novost", region_code="mi", text="a")
    await service.record_verdicts(db_session, [v])
    second = await service.record_verdicts(
        db_session, [ClassifierVerdict(lip="1_10", theme="reklama", region_code="mi", text="a")]
    )
    assert second["recorded"] == 0 and second["skipped_existing"] == 1


# ───────── реакции + agree-rate ─────────


async def _one(db_session, *, theme="novost", action="publish", merge=False):
    v = ClassifierVerdict(
        lip="1_10",
        theme=theme,
        action=action,
        split=merge,
        confidence=70,
        region_code="mi",
        text="t",
    )
    await service.record_verdicts(db_session, [v])
    feed = await service.review_feed(db_session)
    return feed[0]["id"]


@pytest.mark.asyncio
async def test_agree_all_applicable_types(db_session):
    cid = await _one(db_session, merge=True)
    out = await service.agree_all(db_session, cid)
    assert set(out["agreed_types"]) == {"theme", "action", "merge"}
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["theme"]["agree_rate"] == 1.0
    assert stats["by_type"]["merge"]["agree"] == 1


@pytest.mark.asyncio
async def test_agree_skips_merge_when_no_signal(db_session):
    cid = await _one(db_session, merge=False)
    out = await service.agree_all(db_session, cid)
    assert set(out["agreed_types"]) == {"theme", "action"}
    assert (await service.agree_rate_stats(db_session))["by_type"]["merge"]["total"] == 0


@pytest.mark.asyncio
async def test_correction_and_rate(db_session):
    cid = await _one(db_session)
    await service.correct(db_session, cid, verdict_type="theme", operator_value="sport")
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["theme"] == {"agree": 0, "correct": 1, "total": 1, "agree_rate": 0.0}


@pytest.mark.asyncio
async def test_reaction_last_wins(db_session):
    cid = await _one(db_session)
    await service.correct(db_session, cid, verdict_type="action", operator_value="delete")
    await service.agree_all(db_session, cid)  # перезапишет action на agree
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["action"]["agree"] == 1
    assert stats["by_type"]["action"]["correct"] == 0


@pytest.mark.asyncio
async def test_correct_unknown_type_rejected(db_session):
    cid = await _one(db_session)
    out = await service.correct(db_session, cid, verdict_type="bogus", operator_value="x")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_feed_only_unreacted(db_session):
    cid = await _one(db_session)
    assert len(await service.review_feed(db_session, only_unreacted=True)) == 1
    await service.agree_all(db_session, cid)
    assert len(await service.review_feed(db_session, only_unreacted=True)) == 0
    assert len(await service.review_feed(db_session, only_unreacted=False)) == 1
