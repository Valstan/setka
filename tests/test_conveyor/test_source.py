"""Тесты отбора постов для сайта — вход конвейера (D-015).

Держим ровно те свойства, поломка которых стоит денег или репутации сайта:
неопубликованное не уходит, обработанное не переплачивается, префильтр тем
работает, медиа доезжают (без них сайт получит новость без картинок).
"""

from __future__ import annotations

import pytest

from database.models_extended import ConveyorDelivery
from modules.conveyor import source
from tests.test_conveyor.conftest import SITE, seed_audit, seed_pair, seed_run


@pytest.mark.asyncio
async def test_picks_published_posts_with_text_and_media(db_session):
    await seed_pair(db_session, lip="1_10", media=[{"type": "photo", "url": "https://cdn/1.jpg"}])
    out = await source.fetch_pending_for_site(db_session, SITE)
    assert [p["lip"] for p in out] == ["1_10"]
    assert out[0]["text"] == "текст поста"
    assert out[0]["media"] == [{"type": "photo", "url": "https://cdn/1.jpg"}]
    assert out[0]["theme"] == "novost"


@pytest.mark.asyncio
async def test_unpublished_bulletin_is_not_a_source(db_session):
    """Сводка-черновик — это ещё не решение человека, брать её нельзя."""
    await seed_pair(db_session, lip="1_10", published=False)
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_post_without_audit_row_is_skipped(db_session):
    """Нет строки аудита — нет ни текста, ни медиа; отдавать LLM нечего."""
    await seed_run(db_session, lips=["1_10"])
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_empty_text_is_skipped(db_session):
    await seed_pair(db_session, lip="1_10", text="   ")
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_skip_themes_prefilter(db_session):
    """Дешёвый префильтр до LLM: реклама и соседи на районный портал не идут."""
    await seed_pair(db_session, lip="1_10", theme="reklama")
    await seed_pair(db_session, lip="1_20", theme="neighbors")
    await seed_pair(db_session, lip="1_30", theme="kultura")
    out = await source.fetch_pending_for_site(db_session, SITE)
    assert [p["lip"] for p in out] == ["1_30"]


@pytest.mark.asyncio
async def test_other_region_is_not_taken(db_session):
    await seed_run(db_session, lips=["2_10"], region_code="vp")
    await seed_audit(db_session, lip="2_10", region="vp")
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_window_excludes_old(db_session):
    await seed_pair(db_session, lip="1_10", days_ago=30)
    assert await source.fetch_pending_for_site(db_session, SITE, days=3) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["delivered", "rejected", "held", "failed", "selected"])
async def test_any_existing_delivery_row_excludes_post(db_session, status):
    """Пост, по которому решение уже принято, не возвращается ни в каком статусе.

    Особенно rejected/held: иначе каждый прогон переспрашивал бы LLM про то, что
    уже отклонено, и счёт за токены рос бы на ровном месте.
    """
    await seed_pair(db_session, lip="1_10")
    db_session.add(ConveyorDelivery(site="vmalmyzhe", lip="1_10", status=status))
    await db_session.commit()
    assert await source.fetch_pending_for_site(db_session, SITE) == []


@pytest.mark.asyncio
async def test_delivery_of_another_site_does_not_block(db_session):
    """Журнал ведётся по (site, lip) — доставка на ДК не закрывает пост для портала."""
    await seed_pair(db_session, lip="1_10")
    db_session.add(ConveyorDelivery(site="kalinino", lip="1_10", status="delivered"))
    await db_session.commit()
    out = await source.fetch_pending_for_site(db_session, SITE)
    assert [p["lip"] for p in out] == ["1_10"]


@pytest.mark.asyncio
async def test_dedup_across_bulletins(db_session):
    """Один пост в двух сводках — одна запись на выходе."""
    await seed_run(db_session, lips=["1_10"], theme="novost")
    await seed_run(db_session, lips=["1_10"], theme="kultura", days_ago=1)
    await seed_audit(db_session, lip="1_10")
    out = await source.fetch_pending_for_site(db_session, SITE)
    assert [p["lip"] for p in out] == ["1_10"]


@pytest.mark.asyncio
async def test_limit_is_respected(db_session):
    for i in range(5):
        await seed_pair(db_session, lip=f"1_{i}")
    out = await source.fetch_pending_for_site(db_session, SITE, limit=2)
    assert len(out) == 2


@pytest.mark.asyncio
async def test_site_without_region_yields_nothing(db_session):
    """Полуописанный сайт молчит, а не сыплет данными чужого региона."""
    await seed_pair(db_session, lip="1_10")
    broken = dict(SITE, source_region="")
    assert await source.fetch_pending_for_site(db_session, broken) == []


# ───────── record_selection ─────────


@pytest.mark.asyncio
async def test_record_selection_is_idempotent(db_session):
    assert await source.record_selection(db_session, site="vmalmyzhe", lips=["1_10", "1_20"]) == 2
    await db_session.commit()
    assert await source.record_selection(db_session, site="vmalmyzhe", lips=["1_10", "1_30"]) == 1
    await db_session.commit()
    counts = await source.site_status_counts(db_session, site="vmalmyzhe")
    assert counts == {"selected": 3}


@pytest.mark.asyncio
async def test_record_selection_ignores_blanks(db_session):
    assert await source.record_selection(db_session, site="vmalmyzhe", lips=["", "  ", None]) == 0


@pytest.mark.asyncio
async def test_selection_then_fetch_excludes(db_session):
    """Отобранное в прошлом прогоне не приходит снова — цикл сходится."""
    await seed_pair(db_session, lip="1_10")
    first = await source.fetch_pending_for_site(db_session, SITE)
    await source.record_selection(db_session, site="vmalmyzhe", lips=[p["lip"] for p in first])
    await db_session.commit()
    assert await source.fetch_pending_for_site(db_session, SITE) == []


# ───────── summarize_for_prompt ─────────


def test_summarize_mentions_theme_and_media_kinds():
    out = source.summarize_for_prompt(
        {"text": "новость", "theme": "kultura", "media": [{"type": "photo"}, {"type": "doc"}]}
    )
    assert "kultura" in out and "photo" in out and "doc" in out and out.endswith("новость")


def test_summarize_without_text_is_none():
    assert source.summarize_for_prompt({"text": "  ", "theme": "novost"}) is None


def test_summarize_truncates_long_text():
    out = source.summarize_for_prompt({"text": "я" * 5000, "theme": "novost"}, max_chars=100)
    body = out.split("\n", 1)[1]  # считаем хвост, а не всю строку: в шапке своя кириллица
    assert len(body) == 100
