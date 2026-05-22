"""Tests for /api/discovery — region auto-registration (big idea 2026-05-22)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models import Community, CommunityCandidate
from web.api import discovery as discovery_api

# ─── Pydantic validation ─────────────────────────────────────────


def test_trigger_in_validates_categories():
    """Unknown category should be rejected before we even hit Groq."""
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, categories=["bogus_cat"])


def test_trigger_in_accepts_subset_of_allowed_categories():
    payload = discovery_api.TriggerIn(region_id=1, categories=["novost", "sport"])
    assert payload.categories == ["novost", "sport"]


def test_trigger_in_per_query_count_bounds():
    """per_query_count must stay within 10..1000."""
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, per_query_count=5)
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, per_query_count=2000)
    # Within bounds is fine.
    discovery_api.TriggerIn(region_id=1, per_query_count=100)


def test_candidate_patch_accepts_valid_status():
    p = discovery_api.CandidatePatch(status="approved", category="novost")
    assert p.status == "approved"


def test_candidate_patch_rejects_invalid_status():
    with pytest.raises(Exception):
        discovery_api.CandidatePatch(status="bogus")


def test_candidate_patch_normalises_status_case():
    p = discovery_api.CandidatePatch(status="APPROVED", category="novost")
    assert p.status == "approved"


def test_candidate_patch_rejects_unknown_category():
    with pytest.raises(Exception):
        discovery_api.CandidatePatch(status="approved", category="bogus_cat")


def test_candidate_patch_empty_category_becomes_none():
    p = discovery_api.CandidatePatch(status="rejected", category="")
    assert p.category is None


# ─── /cities — VK resolver smoke ─────────────────────────────────


async def test_resolve_city_returns_trimmed_items():
    fake_client = MagicMock()
    fake_client.resolve_city.return_value = [
        {"id": 314, "title": "Малмыж", "area": "Малмыжский р-н", "region": "Кировская обл."},
        {"id": 0, "title": "Skip me — no id"},  # должно отфильтроваться
        {"id": 200},  # минимум полей — заполняем пустыми строками
    ]
    with patch.object(discovery_api, "VK_TOKENS", {"VALSTAN": "token"}):
        with patch.object(discovery_api, "VKClient", return_value=fake_client):
            res = await discovery_api.resolve_city(q="Малмыж")
    items = res["items"]
    assert len(items) == 2
    assert items[0] == {
        "id": 314,
        "title": "Малмыж",
        "area": "Малмыжский р-н",
        "region": "Кировская обл.",
    }
    assert items[1] == {"id": 200, "title": "", "area": "", "region": ""}


async def test_resolve_city_503_when_no_token():
    with patch.object(discovery_api, "VK_TOKENS", {}):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.resolve_city(q="X")
    assert exc.value.status_code == 503


# ─── /trigger — endpoint orchestration ────────────────────────────


async def test_trigger_returns_runner_result_on_success():
    payload = discovery_api.TriggerIn(region_id=7, categories=None)
    fake_result = {"success": True, "region": "mi", "found": 5}
    with patch.object(
        discovery_api,
        "run_discovery_for_region_async",
        AsyncMock(return_value=fake_result),
    ):
        out = await discovery_api.trigger_discovery(payload)
    assert out == fake_result


async def test_trigger_translates_failure_to_http_400():
    payload = discovery_api.TriggerIn(region_id=7)
    with patch.object(
        discovery_api,
        "run_discovery_for_region_async",
        AsyncMock(return_value={"success": False, "error": "region not found"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.trigger_discovery(payload)
    assert exc.value.status_code == 400
    assert "region not found" in exc.value.detail


# ─── candidate PATCH ────────────────────────────────────────────


class _FakeSession:
    """AsyncSessionLocal stand-in (close enough for endpoint smoke tests)."""

    def __init__(self, *, get_result=None, scalars_all=None, execute_scalar=None):
        self._get_result = get_result
        self._scalars_all = scalars_all or []
        self._execute_scalar = execute_scalar
        self.added = []
        self.committed = False
        self.refreshed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        scalars = MagicMock()
        scalars.all.return_value = self._scalars_all
        result = MagicMock()
        result.scalars.return_value = scalars
        result.scalar_one_or_none.return_value = self._execute_scalar
        return result

    async def get(self, _model, _pk):
        return self._get_result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = 999


async def test_patch_candidate_404_when_missing():
    session = _FakeSession(get_result=None)
    payload = discovery_api.CandidatePatch(status="rejected")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(123, payload)
    assert exc.value.status_code == 404


async def test_patch_candidate_reject_just_updates_status():
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="rejected")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_candidate(1, payload)
    assert cand.status == "rejected"
    assert session.committed is True
    assert "candidate" in out


async def test_patch_candidate_approve_requires_category():
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
        ai_category=None,
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(1, payload)
    assert exc.value.status_code == 400
    assert "category" in exc.value.detail.lower()


async def test_patch_candidate_approve_uses_ai_category_when_payload_empty():
    """If client doesn't override category, fall back to AI suggestion."""
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="Test",
        status="pending",
        ai_category="novost",
    )
    session = _FakeSession(get_result=cand, execute_scalar=None)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_candidate(1, payload)
    assert cand.status == "approved"
    # Должно быть создано новое Community.
    assert len(session.added) == 1
    new_community = session.added[0]
    assert isinstance(new_community, Community)
    assert new_community.category == "novost"
    assert "candidate" in out


async def test_patch_candidate_approve_rejects_other_as_category():
    """'other' — это escape hatch, не валидная финальная категория."""
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
        ai_category="other",
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(1, payload)
    assert exc.value.status_code == 400
