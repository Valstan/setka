"""Tests петли обучения классификатора (ADR-0005): выученные правила overlay."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from database.models_extended import (
    ClassificationCorrection,
    ClassificationRule,
    ContentClassification,
)
from modules.classifier import rules
from modules.classifier.schema import RuleProposal


async def _seed_rule(session, *, text, status="proposed", source="routine", norm=None):
    r = ClassificationRule(
        rule_text=text, status=status, source=source, norm_key=(norm or text.lower())
    )
    session.add(r)
    await session.commit()
    return r


# ───────── render_effective_postulates ─────────


@pytest.mark.asyncio
async def test_effective_base_only_when_no_approved(db_session):
    # Только proposed → в эффективные не подмешиваются, база байт-в-байт.
    await _seed_rule(db_session, text="черновик", status="proposed")
    out = await rules.render_effective_postulates(db_session)
    assert "Классификационные постулаты" in out  # база на месте
    assert "Выученные правила" not in out


@pytest.mark.asyncio
async def test_effective_appends_approved(db_session):
    await _seed_rule(db_session, text="Правило про траур", status="approved")
    await _seed_rule(db_session, text="Правило про рекламу", status="approved")
    await _seed_rule(db_session, text="отклонённое", status="rejected")
    out = await rules.render_effective_postulates(db_session)
    assert "Выученные правила" in out
    assert "Правило про траур" in out
    assert "Правило про рекламу" in out
    assert "отклонённое" not in out  # rejected не подмешивается


# ───────── record_rule_proposals ─────────


@pytest.mark.asyncio
async def test_record_proposals_dedup_and_invalid(db_session):
    # уже висит активное правило с тем же смыслом
    await _seed_rule(db_session, text="Соболезнования не мешать со спортом", status="approved")
    props = [
        RuleProposal(rule_text="Соболезнования не мешать со спортом"),  # дубль активного
        RuleProposal(rule_text="  соболезнования  НЕ  мешать  со   спортом "),  # тот же norm
        RuleProposal(rule_text="Новое правило про дубли"),  # новое
        RuleProposal(rule_text="   "),  # пробелы: min_length проходит, после strip пусто → invalid
    ]
    out = await rules.record_rule_proposals(db_session, props)
    assert out["recorded"] == 1
    assert out["skipped_existing"] == 2
    assert out["skipped_invalid"] == 1
    stored = (
        (
            await db_session.execute(
                select(ClassificationRule).where(ClassificationRule.status == "proposed")
            )
        )
        .scalars()
        .all()
    )
    assert len(stored) == 1
    assert stored[0].rule_text == "Новое правило про дубли"


# ───────── decide_rule / add_operator_rule ─────────


@pytest.mark.asyncio
async def test_decide_rule_approve_reject_retire(db_session):
    r = await _seed_rule(db_session, text="черновик правила", status="proposed")
    ok = await rules.decide_rule(db_session, r.id, status="approved")
    assert ok["ok"] and ok["status"] == "approved"
    await db_session.refresh(r)
    assert r.status == "approved" and r.decided_at is not None

    r2 = await _seed_rule(db_session, text="второй", status="proposed")
    await rules.decide_rule(db_session, r2.id, status="rejected")
    await db_session.refresh(r2)
    assert r2.status == "rejected"


@pytest.mark.asyncio
async def test_decide_rule_edit_text_on_approve(db_session):
    r = await _seed_rule(db_session, text="сырой текст", status="proposed")
    await rules.decide_rule(db_session, r.id, status="approved", edited_text="Уточнённый текст")
    await db_session.refresh(r)
    assert r.status == "approved"
    assert r.rule_text == "Уточнённый текст"
    assert r.norm_key == "уточнённый текст"


@pytest.mark.asyncio
async def test_decide_rule_not_found(db_session):
    out = await rules.decide_rule(db_session, 9999, status="approved")
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_add_operator_rule_is_approved_immediately(db_session):
    out = await rules.add_operator_rule(db_session, "Правило руками оператора")
    assert out["ok"] and out["status"] == "approved"
    eff = await rules.render_effective_postulates(db_session)
    assert "Правило руками оператора" in eff  # сразу в эффективных


# ───────── fetch_corrections_for_distill ─────────


async def _seed_correction(db_session, *, lip, outcome="correct", days_ago=0, text="пост про X"):
    cls = ContentClassification(
        lip=lip,
        region_code="mi",
        post_text=text,
        post_url=f"https://vk.com/wall{lip}",
        source="routine",
        verdict={"theme": "novost", "action": "publish"},
    )
    db_session.add(cls)
    await db_session.commit()
    corr = ClassificationCorrection(
        classification_id=cls.id,
        lip=lip,
        verdict_type="action",
        outcome=outcome,
        ai_value="publish",
        operator_value="delete",
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    )
    db_session.add(corr)
    await db_session.commit()
    return cls, corr


@pytest.mark.asyncio
async def test_fetch_corrections_only_correct_within_window(db_session):
    await _seed_correction(db_session, lip="1_10", outcome="correct", days_ago=0)
    await _seed_correction(
        db_session, lip="1_20", outcome="agree", days_ago=0
    )  # согласие — не берём
    await _seed_correction(db_session, lip="1_30", outcome="correct", days_ago=90)  # вне окна
    out = await rules.fetch_corrections_for_distill(db_session, days=30)
    lips = {c["lip"] for c in out}
    assert lips == {"1_10"}
    assert out[0]["ai_value"] == "publish" and out[0]["operator_value"] == "delete"
    assert out[0]["post_text"] == "пост про X"
