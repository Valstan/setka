"""Unit tests for modules/discovery/ai_categorizer.py.

The tests mock out Groq at the import level (``modules.discovery.ai_categorizer``
imports ``Groq`` lazily inside the function), so we patch the symbol in the
module namespace after import.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

import modules.discovery.ai_categorizer as ac


def _fake_completion(content: str) -> SimpleNamespace:
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class _FakeGroq:
    def __init__(self, *, content: str | None = None, raise_exc: Exception | None = None):
        self._content = content
        self._exc = raise_exc

    def __call__(self, *, api_key):  # mimic Groq(api_key=...)
        outer = self

        class _Chat:
            class completions:
                @staticmethod
                def create(**_kwargs):
                    if outer._exc is not None:
                        raise outer._exc
                    return _fake_completion(outer._content or "")

        return SimpleNamespace(chat=_Chat())


def _install_fake_groq(monkeypatch, *, content=None, raise_exc=None):
    """Inject a fake groq module so `from groq import Groq` returns our class."""
    fake_module = SimpleNamespace(Groq=_FakeGroq(content=content, raise_exc=raise_exc))
    monkeypatch.setitem(sys.modules, "groq", fake_module)


# ───────── pure helpers ─────────


def test_strip_json_fences_unwraps_json_block():
    assert ac._strip_json_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_json_fences_passthrough_plain():
    assert ac._strip_json_fences('{"a": 1}') == '{"a": 1}'


def test_parse_response_handles_plain_json():
    parsed = ac._parse_response('{"category": "novost", "confidence": 80}')
    assert parsed == {"category": "novost", "confidence": 80}


def test_parse_response_extracts_json_after_garbage_text():
    parsed = ac._parse_response('Хорошо, вот ответ:\n{"category": "sport"}\nспасибо')
    assert parsed == {"category": "sport"}


def test_parse_response_returns_none_on_unrecoverable():
    assert ac._parse_response("nope, just text") is None


def test_normalise_clamps_confidence_and_validates_category():
    out = ac._normalise({"category": "FAKE_CAT", "confidence": 150})
    assert out["category"] == "other"
    assert out["confidence"] == 100


def test_normalise_negative_confidence_clamps_to_zero():
    out = ac._normalise({"category": "sport", "confidence": -5})
    assert out["confidence"] == 0


def test_normalise_keeps_known_category():
    out = ac._normalise({"category": "novost", "confidence": 73, "is_info_page": True})
    assert out["category"] == "novost"
    assert out["confidence"] == 73
    assert out["is_info_page"] is True


# ───────── categorize_candidate (end-to-end with mocked Groq) ─────────


@pytest.mark.asyncio
async def test_categorize_empty_name_short_circuits():
    res = await ac.categorize_candidate(name="")
    assert res == {"success": False, "error": "name is empty"}


@pytest.mark.asyncio
async def test_categorize_no_api_key(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "")
    res = await ac.categorize_candidate(name="Test")
    assert res["success"] is False
    assert "GROQ_API_KEY" in res["error"]


@pytest.mark.asyncio
async def test_categorize_happy_path(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "fake")
    _install_fake_groq(
        monkeypatch,
        content=json.dumps(
            {
                "category": "novost",
                "confidence": 88,
                "is_info_page": True,
                "reasoning": "Постит ежедневные новости района",
            }
        ),
    )
    res = await ac.categorize_candidate(
        name="МАЛМЫЖ-ИНФО",
        description="Главные новости района",
        members_count=12000,
        recent_posts=["Сегодня в Малмыже..."],
        region_name="Малмыж",
    )
    assert res["success"] is True
    assert res["category"] == "novost"
    assert res["confidence"] == 88
    assert res["is_info_page"] is True
    assert "район" in res["reasoning"]
    assert res["model"] == ac._MODEL


@pytest.mark.asyncio
async def test_categorize_unknown_category_falls_back_to_other(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "fake")
    _install_fake_groq(monkeypatch, content='{"category": "unknown_xyz", "confidence": 50}')
    res = await ac.categorize_candidate(name="X")
    assert res["success"] is True
    assert res["category"] == "other"


@pytest.mark.asyncio
async def test_categorize_groq_exception_returns_failure(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "fake")
    _install_fake_groq(monkeypatch, raise_exc=RuntimeError("network"))
    res = await ac.categorize_candidate(name="X")
    assert res["success"] is False
    assert "network" in res["error"]


@pytest.mark.asyncio
async def test_categorize_empty_response_failure(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "fake")
    _install_fake_groq(monkeypatch, content="")
    res = await ac.categorize_candidate(name="X")
    assert res["success"] is False
    assert "empty" in res["error"].lower()


@pytest.mark.asyncio
async def test_categorize_garbage_json_failure_with_raw(monkeypatch):
    monkeypatch.setattr(ac, "GROQ_API_KEY", "fake")
    _install_fake_groq(monkeypatch, content="this is not json at all")
    res = await ac.categorize_candidate(name="X")
    assert res["success"] is False
    assert "valid JSON" in res["error"]
    assert "raw" in res
