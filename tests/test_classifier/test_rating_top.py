"""Витрина топ-N по рейтингу (звено 5, шаг 1) — ранжирование и HTTP.

HTTP-слой проверяется отдельно и намеренно: инцидент 2026-08-19 показал, что
групповые ручки этого же роутера были покрыты на уровне сервиса и при этом
недостижимы снаружи (статический путь затенялся параметризованным).

Ниже же — часть, добавленная по находкам ревью (#495 handoff): ветка
``alphas=None`` (порядок и дедуп колонок) и SQL-часть ``top_by_rating`` (окно
72 часов, ``published_at IS NULL``, пересечение с вердиктом, фильтр темы,
разбивка по ``decision``) раньше были проверены только чтением кода.
"""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import config.classifier as config_classifier_mod
from database.models_extended import CollectedPostAudit, ContentClassification
from modules.classifier.rating import rank_rows, top_by_rating
from web.api import classifier_review as rev


def _row(lip, views, likes=0, comments=0, reposts=0):
    return {"lip": lip, "views": views, "likes": likes, "comments": comments, "reposts": reposts}


def test_rank_sorts_by_score_descending():
    rows = [_row("a", 10000, 100), _row("b", 20, 12)]
    out = rank_rows(rows, alpha=0.25, n=10)
    assert [r["lip"] for r in out] == ["a", "b"]
    out_half = rank_rows(rows, alpha=0.5, n=10)
    assert [r["lip"] for r in out_half] == ["b", "a"]


def test_rank_puts_unmeasured_views_last():
    # score=None — «не мерили». Такой пост не должен обгонять измеренные,
    # каким бы ни было число лайков.
    rows = [_row("no-views", None, 999), _row("measured", 100, 1)]
    out = rank_rows(rows, alpha=0.25, n=10)
    assert [r["lip"] for r in out] == ["measured", "no-views"]
    assert out[-1]["score"] is None


def test_rank_respects_n():
    rows = [_row(str(i), 100, i) for i in range(20)]
    assert len(rank_rows(rows, alpha=0.25, n=5)) == 5


def test_rank_on_empty_input_is_empty():
    assert rank_rows([], alpha=0.25, n=10) == []


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(rev.router, prefix="/api/classifier-review")
    return TestClient(app)


def test_endpoint_requires_region(client):
    # Рейтинги районов между собой несопоставимы — общий топ был бы бессмыслен.
    r = client.get("/api/classifier-review/rating/top")
    assert r.status_code == 422, r.text


def test_endpoint_reaches_the_service_and_not_the_id_route(client):
    fake = AsyncMock(return_value={"region": "mi", "alphas": {}, "rows": []})
    with patch.object(rev.rating, "top_by_rating", fake):
        r = client.get("/api/classifier-review/rating/top?region=mi&n=5")
    assert r.status_code == 200, r.text
    assert fake.await_count == 1
    assert fake.await_args.kwargs["region_code"] == "mi"
    assert fake.await_args.kwargs["n"] == 5


def test_endpoint_rejects_whitespace_only_region(client):
    # min_length=1 пропускает " " — .strip() внутри дал бы пустой region и
    # тихий пустой топ вместо ошибки. region=" " не должен доехать до сервиса.
    r = client.get("/api/classifier-review/rating/top?region=%20&n=5")
    assert r.status_code == 422, r.text


# --- alphas=None: порядок и дедуп колонок --------------------------------------
#
# Замена dict.fromkeys на set() потеряла бы порядок колонок тихо — эти тесты
# существуют ровно чтобы такую замену ловил CI, а не глаз владельца на панели.


def _audit(
    lip,
    *,
    region="mi",
    decision="kept",
    theme="novost",
    views=100,
    likes=1,
    comments=0,
    reposts=0,
    published_at=None,
    collected_at=None,
):
    """Строка аудита сбора — источник кандидатов витрины."""
    return CollectedPostAudit(
        lip=lip,
        region_code=region,
        theme=theme,
        post_url=f"https://vk.com/wall{lip}",
        decision=decision,
        views=views,
        likes=likes,
        comments=comments,
        reposts=reposts,
        published_at=published_at,
        collected_at=collected_at or datetime.utcnow(),
    )


def _verdict(lip, *, region="mi", action="publish"):
    """Вердикт ИИ — источник ``selection.fetch_publish_lips``: без него ни
    один lip не пройдёт в выдачу, даже свежий и в окне."""
    return ContentClassification(
        lip=lip,
        region_code=region,
        post_text="t",
        post_url="u",
        source="routine",
        verdict={"theme": "novost", "action": action},
        shadow=True,
    )


async def _seed(session, rows):
    for r in rows:
        session.add(r)
    await session.commit()


@pytest.mark.asyncio
async def test_default_alphas_lead_with_the_configured_value(db_session, monkeypatch):
    # 0.01 выбрано не произвольно: для {0.01, 0.5, 0.0} обычный set() кладёт
    # 0.5 первым (порядок хеш-бакетов), а dict.fromkeys — сохраняет вставку.
    # Более «круглые» значения (0.1, 0.25...) с set() совпали бы с нужным
    # порядком случайно и не поймали бы регресс — проверено вручную.
    monkeypatch.setattr(config_classifier_mod, "get_rating_views_alpha", lambda: 0.01)
    now = datetime.utcnow()
    await _seed(db_session, [_audit("a_1", published_at=now - timedelta(hours=1)), _verdict("a_1")])

    out = await top_by_rating(db_session, region_code="mi", n=10)

    assert list(out["alphas"].keys()) == ["0.01", "0.5", "0.0"]


@pytest.mark.asyncio
async def test_default_alphas_dedup_when_configured_equals_half(db_session, monkeypatch):
    # Вырожденный случай: настроенная alpha совпадает с опорной 0.5 — дубля
    # быть не должно, порядок остаётся [0.5, 0.0].
    monkeypatch.setattr(config_classifier_mod, "get_rating_views_alpha", lambda: 0.5)
    now = datetime.utcnow()
    await _seed(db_session, [_audit("a_2", published_at=now - timedelta(hours=1)), _verdict("a_2")])

    out = await top_by_rating(db_session, region_code="mi", n=10)

    assert list(out["alphas"].keys()) == ["0.5", "0.0"]


@pytest.mark.asyncio
async def test_default_alphas_dedup_when_configured_equals_zero(db_session, monkeypatch):
    # Второй вырожденный случай: настроенная alpha совпадает с опорной 0.0 —
    # дубль должен схлопнуться, а не 0.0 остаться на своём обычном месте.
    monkeypatch.setattr(config_classifier_mod, "get_rating_views_alpha", lambda: 0.0)
    now = datetime.utcnow()
    await _seed(db_session, [_audit("a_3", published_at=now - timedelta(hours=1)), _verdict("a_3")])

    out = await top_by_rating(db_session, region_code="mi", n=10)

    assert list(out["alphas"].keys()) == ["0.0", "0.5"]


# --- SQL-часть top_by_rating: окно, NULL published_at, вердикт, тема ----------


@pytest.mark.asyncio
async def test_skips_posts_older_than_72h_window(db_session):
    now = datetime.utcnow()
    old = _audit("b_1", published_at=now - timedelta(hours=100))
    await _seed(db_session, [old, _verdict("b_1")])

    out = await top_by_rating(db_session, region_code="mi", n=10, alphas=[0.25])

    assert out["candidates"] == 0


@pytest.mark.asyncio
async def test_includes_null_published_at_with_fresh_collected_at(db_session):
    # Запасная ветка: без published_at (наследие до миграции 080) решает
    # collected_at — без второй ветки OR такие строки терялись бы молча.
    now = datetime.utcnow()
    row = _audit("b_2", published_at=None, collected_at=now - timedelta(hours=1))
    await _seed(db_session, [row, _verdict("b_2")])

    out = await top_by_rating(db_session, region_code="mi", n=10, alphas=[0.25])

    assert out["candidates"] == 1
    assert out["alphas"]["0.25"][0]["lip"] == "b_2"


@pytest.mark.asyncio
async def test_excludes_lips_not_allowed_by_verdict(db_session):
    now = datetime.utcnow()
    allowed = _audit("b_3", published_at=now - timedelta(hours=1))
    blocked = _audit("b_4", published_at=now - timedelta(hours=1))
    await _seed(
        db_session,
        [allowed, blocked, _verdict("b_3", action="publish"), _verdict("b_4", action="delete")],
    )

    out = await top_by_rating(db_session, region_code="mi", n=10, alphas=[0.25])

    assert out["candidates"] == 1
    lips = {r["lip"] for r in out["alphas"]["0.25"]}
    assert lips == {"b_3"}


@pytest.mark.asyncio
async def test_filters_by_theme(db_session):
    now = datetime.utcnow()
    novost = _audit("b_5", theme="novost", published_at=now - timedelta(hours=1))
    afisha = _audit("b_6", theme="afisha", published_at=now - timedelta(hours=1))
    await _seed(db_session, [novost, afisha, _verdict("b_5"), _verdict("b_6")])

    out = await top_by_rating(db_session, region_code="mi", theme="novost", n=10, alphas=[0.25])

    assert out["candidates"] == 1
    assert out["alphas"]["0.25"][0]["lip"] == "b_5"


@pytest.mark.asyncio
async def test_reports_how_many_shown_candidates_were_dropped_by_filters(db_session):
    # Находка ревью: витрина показывает и то, что алгоритмы уже отсеяли
    # (D-024) — молча это вводит в заблуждение, разбивка обязана быть рядом.
    now = datetime.utcnow()
    kept = _audit("b_7", decision="kept", published_at=now - timedelta(hours=1))
    dropped = _audit("b_8", decision="dropped", published_at=now - timedelta(hours=1))
    await _seed(db_session, [kept, dropped, _verdict("b_7"), _verdict("b_8")])

    out = await top_by_rating(db_session, region_code="mi", n=10, alphas=[0.25])

    assert out["candidates"] == 2
    assert out["dropped_by_filters"] == 1
