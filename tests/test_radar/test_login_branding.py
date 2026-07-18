"""Тесты брендинга страницы единого входа (modules/radar_id/branding.py)."""

from unittest.mock import MagicMock, patch

import pytest

from modules.radar_id import branding


def test_client_id_parsed_from_oidc_next():
    nxt = "/oidc/authorize?response_type=code&client_id=sabantuy&scope=openid"
    assert branding._client_id_from_next(nxt) == "sabantuy"


def test_client_id_ignores_foreign_and_absolute_urls():
    assert branding._client_id_from_next(None) is None
    assert branding._client_id_from_next("/radar") is None
    assert branding._client_id_from_next("https://evil.example/oidc/authorize?client_id=x") is None
    assert branding._client_id_from_next("//evil.example/oidc/authorize?client_id=x") is None
    assert branding._client_id_from_next("/oidc/authorize") is None


@pytest.mark.asyncio
async def test_resolve_brand_default():
    brand = await branding.resolve_brand(None, "xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai")
    assert brand == branding.DEFAULT_BRAND


@pytest.mark.asyncio
async def test_resolve_brand_radar_host():
    brand = await branding.resolve_brand(None, "xn--80aal0cd.xn--80adkdyec4j.xn--p1ai:443")
    assert brand["title"] == "Радар"
    assert brand["icon"] == "📡"


@pytest.mark.asyncio
async def test_resolve_brand_oidc_client_with_branding():
    client = MagicMock()
    client.name = "Тренер"
    client.branding = {"title": "САБАНТУЙ в Малмыже", "icon": "🎪", "accent": "#c0392b"}

    class _FakeResult:
        def scalar_one_or_none(self):
            return client

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **kw):
            return _FakeResult()

    with patch("database.connection.AsyncSessionLocal", lambda: _FakeSession()):
        brand = await branding.resolve_brand("/oidc/authorize?client_id=sabantuy", "any-host")
    assert brand["title"] == "САБАНТУЙ в Малмыже"
    assert brand["icon"] == "🎪"
    assert brand["accent"] == "#c0392b"
    assert brand["sub"] == branding.DEFAULT_BRAND["sub"]  # незаданное — из дефолта


@pytest.mark.asyncio
async def test_resolve_brand_oidc_client_without_branding_uses_name():
    client = MagicMock()
    client.name = "trener"
    client.branding = None

    class _FakeResult:
        def scalar_one_or_none(self):
            return client

    class _FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def execute(self, *a, **kw):
            return _FakeResult()

    with patch("database.connection.AsyncSessionLocal", lambda: _FakeSession()):
        brand = await branding.resolve_brand("/oidc/authorize?client_id=trener", "h")
    assert brand["title"] == "trener"


@pytest.mark.asyncio
async def test_resolve_brand_db_error_is_fail_open():
    class _Boom:
        def __call__(self):
            raise RuntimeError("db down")

    with patch("database.connection.AsyncSessionLocal", _Boom()):
        brand = await branding.resolve_brand("/oidc/authorize?client_id=x", "h")
    assert brand == branding.DEFAULT_BRAND
