"""Tests ingest-API классификатора (web/api/classifier_ingest.py) — auth, gate, эндпоинты.

Мини-FastAPI + TestClient; service замокан (без БД).
"""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.api import classifier_ingest as ing

KEY = "routine-secret"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("CLASSIFIER_INGEST_KEY", KEY)
    monkeypatch.delenv("CLASSIFIER_DISABLED", raising=False)
    monkeypatch.delenv("CLASSIFIER_REGION_CODES", raising=False)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(ing.router, prefix="/api/classifier")
    return TestClient(app)


def test_pending_requires_key(client):
    assert client.get("/api/classifier/pending").status_code == 401
    assert client.get("/api/classifier/pending", headers={"X-API-Key": "nope"}).status_code == 401


def test_pending_ok_with_key(client):
    fake = AsyncMock(return_value=[{"lip": "1_10", "text": "a"}])
    with patch.object(ing.service, "fetch_pending", fake):
        r = client.get("/api/classifier/pending?limit=5", headers={"X-API-Key": KEY})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 1 and body["posts"][0]["lip"] == "1_10"


def test_pending_uses_region_query_over_allowlist(client, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_REGION_CODES", "vp")
    fake = AsyncMock(return_value=[])
    with patch.object(ing.service, "fetch_pending", fake):
        client.get("/api/classifier/pending?region=mi", headers={"X-API-Key": KEY})
    # region-query перекрывает allowlist
    assert fake.await_args.kwargs["region_codes"] == ["mi"]


def test_pending_passes_source_days_default(client):
    # По умолчанию окно свежести — 3 суток (модель владельца «не старше 3 дней»).
    fake = AsyncMock(return_value=[])
    with patch.object(ing.service, "fetch_pending", fake):
        client.get("/api/classifier/pending", headers={"X-API-Key": KEY})
    assert fake.await_args.kwargs["days"] == 3


def test_pending_source_days_env_override(client, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_SOURCE_DAYS", "5")
    fake = AsyncMock(return_value=[])
    with patch.object(ing.service, "fetch_pending", fake):
        client.get("/api/classifier/pending", headers={"X-API-Key": KEY})
    assert fake.await_args.kwargs["days"] == 5


def test_verdicts_requires_key(client):
    r = client.post("/api/classifier/verdicts", json={"verdicts": []})
    assert r.status_code == 401


def test_verdicts_records(client):
    fake = AsyncMock(return_value={"recorded": 1, "skipped_existing": 0, "skipped_missing": 0})
    payload = {
        "verdicts": [{"lip": "1_10", "theme": "novost", "action": "publish", "confidence": 80}]
    }
    with patch.object(ing.service, "record_verdicts", fake):
        r = client.post("/api/classifier/verdicts", json=payload, headers={"X-API-Key": KEY})
    assert r.status_code == 200
    assert r.json()["recorded"] == 1
    # source='routine' проставлен
    assert fake.await_args.kwargs["source"] == "routine"


def test_verdicts_bad_action_rejected_by_schema(client):
    # confidence вне диапазона → 422 (pydantic), до записи не доходит
    payload = {"verdicts": [{"lip": "1_10", "theme": "t", "confidence": 500}]}
    r = client.post("/api/classifier/verdicts", json=payload, headers={"X-API-Key": KEY})
    assert r.status_code == 422


def test_kill_switch_503(client, monkeypatch):
    monkeypatch.setenv("CLASSIFIER_DISABLED", "1")
    r = client.get("/api/classifier/pending", headers={"X-API-Key": KEY})
    assert r.status_code == 503


def test_postulates_served(client):
    r = client.get("/api/classifier/postulates")
    assert r.status_code == 200
    assert "Классификационные постулаты" in r.text
