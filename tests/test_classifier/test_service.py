"""Tests доменного ядра классификатора (источник = свод­ки, ключ = lip)."""

from __future__ import annotations

import pytest

from database.models_extended import CollectedPostAudit
from modules.classifier import service
from modules.classifier.schema import ClassifierVerdict
from tests.test_classifier.conftest import _cand, seed_run


async def _seed_audit(db_session, *, lip, region="mi", decision="kept", reason=None, text="t"):
    db_session.add(
        CollectedPostAudit(
            lip=lip,
            region_code=region,
            post_text=text,
            post_url=f"https://vk.com/wall{lip}",
            has_media=False,
            decision=decision,
            drop_reason=reason,
        )
    )
    await db_session.commit()


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
async def test_themes_list_frequency_and_operator_weight(db_session):
    # два вердикта novost, один sport; правка оператора kultura (вес ×2)
    await seed_run(db_session, candidates=[_cand("1_10"), _cand("1_20"), _cand("1_30")])
    await service.record_verdicts(
        db_session,
        [
            ClassifierVerdict(lip="1_10", theme="novost", region_code="mi", text="a"),
            ClassifierVerdict(lip="1_20", theme="novost", region_code="mi", text="b"),
            ClassifierVerdict(lip="1_30", theme="sport", region_code="mi", text="c"),
        ],
    )
    feed = await service.review_feed(db_session)
    await service.correct(db_session, feed[0]["id"], verdict_type="theme", operator_value="kultura")
    themes = await service.themes_list(db_session)
    as_map = {t["theme"]: t["count"] for t in themes}
    assert as_map == {"novost": 2, "sport": 1, "kultura": 2}
    # правка оператора с весом 2 наравне с двумя вердиктами, порядок стабильный
    assert [t["theme"] for t in themes][:2] == ["kultura", "novost"]


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
async def test_feed_only_unreviewed(db_session):
    cid = await _one(db_session)
    assert len(await service.review_feed(db_session, only_unreviewed=True)) == 1
    await service.agree_all(db_session, cid)
    assert len(await service.review_feed(db_session, only_unreviewed=True)) == 0
    assert len(await service.review_feed(db_session, only_unreviewed=False)) == 1


@pytest.mark.asyncio
async def test_feed_stays_until_finalized(db_session):
    # Правка НЕ убирает пост из ленты (можно внести составной вердикт).
    cid = await _one(db_session)
    await service.correct(db_session, cid, verdict_type="theme", operator_value="sport")
    assert len(await service.review_feed(db_session, only_unreviewed=True)) == 1  # ещё в ленте
    await service.finalize(db_session, cid)
    assert len(await service.review_feed(db_session, only_unreviewed=True)) == 0  # ушёл


@pytest.mark.asyncio
async def test_finalize_auto_agrees_untouched(db_session):
    # Поправили тему; finalize → action получает agree, тема остаётся correct.
    cid = await _one(db_session)
    await service.correct(db_session, cid, verdict_type="theme", operator_value="sport")
    out = await service.finalize(db_session, cid)
    assert out["auto_agreed_types"] == ["action"]  # тема не трогается, merge нет сигнала
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["theme"]["correct"] == 1
    assert stats["by_type"]["action"]["agree"] == 1


@pytest.mark.asyncio
async def test_fetch_pending_prefers_audit_source(db_session):
    # Аудит сбора (ADR-0004) имеет приоритет над журналом курации и несёт решение фильтра.
    await _seed_audit(
        db_session, lip="1_10", decision="dropped", reason="advertisement", text="продам"
    )
    out = await service.fetch_pending(db_session, region_codes=["mi"], limit=10)
    assert {p["lip"] for p in out} == {"1_10"}
    assert out[0]["decision"] == "dropped"
    assert out[0]["drop_reason"] == "advertisement"


@pytest.mark.asyncio
async def test_fetch_pending_falls_back_to_curation(db_session):
    # Нет аудита → источник = журнал курации (переходный период).
    await seed_run(db_session, region_code="mi", candidates=[_cand("1_10")])
    out = await service.fetch_pending(db_session, region_codes=["mi"], limit=10)
    assert {p["lip"] for p in out} == {"1_10"}


@pytest.mark.asyncio
async def test_review_feed_attaches_filter_decision(db_session):
    await _seed_audit(db_session, lip="1_20", decision="dropped", reason="advertisement", text="x")
    await service.record_verdicts(
        db_session, [ClassifierVerdict(lip="1_20", theme="reklama", region_code="mi", text="x")]
    )
    feed = await service.review_feed(db_session, region_code="mi")
    item = next(i for i in feed if i["lip"] == "1_20")
    assert item["filter_decision"] == "dropped"
    assert item["filter_reason"] == "advertisement"


@pytest.mark.asyncio
async def test_correct_matching_ai_counts_as_agree(db_session):
    # Клик «→ публиковать» на посте, где ИИ уже publish → согласие, не ложная правка.
    cid = await _one(db_session, action="publish")
    out = await service.correct(db_session, cid, verdict_type="action", operator_value="publish")
    assert out["outcome"] == "agree"
    stats = await service.agree_rate_stats(db_session)
    assert stats["by_type"]["action"] == {"agree": 1, "correct": 0, "total": 1, "agree_rate": 1.0}


# ───────── fair regional batch + health (мультирегион 2026-07-16) ─────────


@pytest.mark.asyncio
async def test_pending_grouped_by_region_fair_share(db_session):
    # backlog: 3 поста region A, 3 поста region B, лимит 4 → по 2 каждому,
    # батч блоками по региону (не чересполосица).
    for i in range(3):
        await _seed_audit(db_session, lip=f"1_{i}", region="bal")
        await _seed_audit(db_session, lip=f"2_{i}", region="mi")
    out = await service.fetch_pending(db_session, limit=4)
    regions = [p["region_code"] for p in out]
    assert regions == ["bal", "bal", "mi", "mi"]


@pytest.mark.asyncio
async def test_pending_round_robin_no_starvation(db_session):
    # 10 постов региона-гиганта против 1 поста малого — малый не голодает.
    for i in range(10):
        await _seed_audit(db_session, lip=f"3_{i}", region="tatarstan_obl")
    await _seed_audit(db_session, lip="4_1", region="tuzha")
    out = await service.fetch_pending(db_session, limit=5)
    regions = [p["region_code"] for p in out]
    assert "tuzha" in regions
    assert regions == sorted(regions)  # блоками по региону


@pytest.mark.asyncio
async def test_health_stats_backlog_and_throughput(db_session):
    await _seed_audit(db_session, lip="5_1", region="mi")
    await _seed_audit(db_session, lip="5_2", region="mi")
    await _seed_audit(db_session, lip="5_3", region="vp")
    await service.record_verdicts(
        db_session, [ClassifierVerdict(lip="5_1", theme="t", region_code="mi", text="x")]
    )
    out = await service.health_stats(db_session)
    assert out["collected_in_window"] == 3
    assert out["classified_in_window"] == 1
    assert out["backlog"] == 2
    assert out["backlog_by_region"] == {"mi": 1, "vp": 1}
    assert out["verdicts_24h"] == 1
    assert out["verdicts_24h_by_region"] == {"mi": 1}
    assert out["coverage_pct"] == pytest.approx(33.3, abs=0.1)
    assert out["last_verdict_at"] is not None
