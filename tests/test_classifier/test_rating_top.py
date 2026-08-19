"""Витрина топ-N по рейтингу (звено 5, шаг 1) — ранжирование и HTTP.

HTTP-слой проверяется отдельно и намеренно: инцидент 2026-08-19 показал, что
групповые ручки этого же роутера были покрыты на уровне сервиса и при этом
недостижимы снаружи (статический путь затенялся параметризованным).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from modules.classifier.rating import rank_rows
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
