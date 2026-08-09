"""Тесты прогона конвейера — склейка трёх звеньев.

Ни одного живого вызова: и классификация, и доставка подменяются. Проверяем
маршрутизацию исходов по журналу — потому что журнал и есть ответ на вопрос
«почему этой новости нет на сайте», и ошибка в нём делает конвейер немым.
"""

from __future__ import annotations

import pytest

from modules.conveyor import runner, source
from tests.test_conveyor.conftest import SITE, seed_pair

LONG_TEXT = (
    "В Малмыже отремонтировали участок дороги по улице Ленина. Работы шли две недели, "
    "подрядчик уложил новое покрытие и обновил разметку у школы номер один."
)


def _accept(**over):
    v = {"action": "accept", "section": "novosti", "title": "Заголовок", "text": LONG_TEXT}
    v.update(over)
    return {"ok": True, "verdict": {**v, "section_known": True}, "usage": {"total_tokens": 100}}


@pytest.fixture
def wired(monkeypatch):
    """Подменяет обе внешние границы: LLM и HTTP-доставку."""

    calls = {"classify": [], "deliver": []}

    def fake_classify(post, *, sections, rules="", api_key=None):
        calls["classify"].append(post["lip"])
        return calls.get("classify_result", _accept())

    def fake_deliver(site, key, body, **kw):
        calls["deliver"].append(body["vkPostId"])
        return calls.get(
            "deliver_result", {"ok": True, "status": 201, "attempts": 1, "remote_id": "r1"}
        )

    monkeypatch.setattr(runner.classify_mod, "classify", fake_classify)
    monkeypatch.setattr(runner.delivery_mod, "deliver", fake_deliver)
    return calls


async def _journal(db_session, lip):
    from sqlalchemy import select

    from database.models_extended import ConveyorDelivery

    return (
        await db_session.execute(select(ConveyorDelivery).where(ConveyorDelivery.lip == lip))
    ).scalar_one()


@pytest.mark.asyncio
async def test_happy_path_marks_delivered(db_session, wired, monkeypatch):
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE, sleep=None)
    assert stats["selected"] == 1 and stats["delivered"] == 1
    row = await _journal(db_session, "1_10")
    assert row.status == "delivered" and row.remote_id == "r1" and row.http_status == 201
    assert row.verdict["title"] == "Заголовок"


@pytest.mark.asyncio
async def test_dry_run_touches_nothing(db_session, wired):
    """Сухой прогон показывает, что улетит, до того как улетит."""
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE, dry_run=True)
    assert stats["selected"] == 1 and stats["delivered"] == 0
    assert stats["preview"][0]["lip"] == "1_10"
    assert wired["classify"] == [] and wired["deliver"] == []
    assert await source.site_status_counts(db_session, site="vmalmyzhe") == {}


@pytest.mark.asyncio
async def test_llm_reject_closes_post(db_session, wired, monkeypatch):
    """Отказ модели — решение: пост закрывается и в следующий прогон не вернётся."""
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    wired["classify_result"] = {"ok": False, "reason": "llm_reject:реклама"}
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["rejected"] == 1 and wired["deliver"] == []
    row = await _journal(db_session, "1_10")
    assert row.status == "rejected" and "реклама" in row.reason
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_llm_network_failure_is_failed_not_rejected(db_session, wired, monkeypatch):
    """Сбой связи — не решение модели, и путать их нельзя: разные статусы разбора."""
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    wired["classify_result"] = {"ok": False, "reason": "network"}
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["failed"] == 1
    row = await _journal(db_session, "1_10")
    assert row.status == "failed" and row.reason == "network"


@pytest.mark.asyncio
async def test_invariant_holds_garbage_before_delivery(db_session, wired, monkeypatch):
    """Мусор задерживается ДО отправки и остаётся видимым человеку."""
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    wired["classify_result"] = _accept(text="Здравствуйте, {author_name}! " + LONG_TEXT)
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["held"] == 1 and wired["deliver"] == []
    row = await _journal(db_session, "1_10")
    assert row.status == "held" and row.reason == "unresolved_placeholder"
    assert row.verdict is not None  # вердикт сохранён — есть что показать человеку


@pytest.mark.asyncio
async def test_delivery_failure_is_recorded_with_attempts(db_session, wired, monkeypatch):
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    wired["deliver_result"] = {"ok": False, "status": 503, "attempts": 3, "reason": "http_503"}
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["failed"] == 1
    row = await _journal(db_session, "1_10")
    assert row.status == "failed" and row.attempts == 3 and row.http_status == 503


@pytest.mark.asyncio
async def test_one_bad_post_does_not_stop_the_run(db_session, wired, monkeypatch):
    """Падение на одном посте не рушит прогон — остальные доезжают."""
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")

    def boom(post, *, sections, rules="", api_key=None):
        if post["lip"] == "1_10":
            raise RuntimeError("модель икнула")
        return _accept()

    monkeypatch.setattr(runner.classify_mod, "classify", boom)
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    await seed_pair(db_session, lip="1_20", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["selected"] == 2 and stats["delivered"] == 1 and stats["failed"] == 1
    assert (await _journal(db_session, "1_10")).reason == "classify_crashed"


@pytest.mark.asyncio
async def test_tokens_are_accounted(db_session, wired, monkeypatch):
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    await seed_pair(db_session, lip="1_20", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["tokens"] == 200


@pytest.mark.asyncio
async def test_unknown_sections_are_collected_for_feedback(db_session, wired, monkeypatch):
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    wired["classify_result"] = {
        "ok": True,
        "verdict": {
            "action": "accept",
            "section": "sport",
            "title": "З",
            "text": LONG_TEXT,
            "section_known": False,
        },
        "usage": {"total_tokens": 10},
    }
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    stats = await runner.run_site(db_session, SITE)
    assert stats["unknown_sections"] == ["sport"]


@pytest.mark.asyncio
async def test_empty_source_is_a_no_op(db_session, wired):
    stats = await runner.run_site(db_session, SITE)
    assert stats["selected"] == 0 and wired["classify"] == []


@pytest.mark.asyncio
async def test_processed_posts_do_not_come_back(db_session, wired, monkeypatch):
    """Второй прогон подряд не переплачивает за уже обработанное."""
    monkeypatch.setenv("VMALMYZHE_INGEST_KEY", "k")
    await seed_pair(db_session, lip="1_10", text=LONG_TEXT)
    await runner.run_site(db_session, SITE)
    wired["classify"].clear()
    stats = await runner.run_site(db_session, SITE)
    assert stats["selected"] == 0 and wired["classify"] == []
