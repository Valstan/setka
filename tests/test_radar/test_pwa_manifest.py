"""PWA Радара: манифест по хосту, id приложения, адрес входа через ЕСА.

Манифест раньше был статическим файлом с жёстким ``/radar``; на
радар.вмалмыже.рф Радар живёт на корне, и установленное приложение стартовало
через редирект. Теперь манифест — маршрут, а ``id`` держит идентичность
приложения при смене ``start_url``: Chrome сравнивает установленные PWA по
``id``, и без него переезд start_url означал бы «второе приложение».
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

import main
from modules.radar_id.vk_upstream import RADAR_CANONICAL_HOST_DEFAULT

_TECH_HOST = "3931b3fe50ab.vps.myjino.ru"


class _URL:
    def __init__(self, hostname: str):
        self.hostname = hostname
        self.path = "/"
        self.query = ""


class _Request:
    headers: dict = {}
    query_params: dict = {}
    cookies: dict = {}

    def __init__(self, hostname: str):
        self.url = _URL(hostname)


@pytest.fixture(autouse=True)
def _issuer(monkeypatch):
    monkeypatch.setenv("RADAR_ID_ISSUER", "https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai")
    monkeypatch.delenv("RADAR_CANONICAL_HOST", raising=False)


# ─── манифест ───────────────────────────────────────────────────


def test_manifest_on_radar_host_starts_at_root_but_keeps_id():
    m = main.radar_manifest(at_root=True)
    assert m["start_url"] == "/" and m["scope"] == "/"
    assert m["id"] == "/radar"  # идентичность приложения не переезжает


def test_manifest_elsewhere_lives_under_radar():
    m = main.radar_manifest(at_root=False)
    assert m["start_url"] == "/radar" and m["scope"] == "/radar"
    assert m["id"] == "/radar"


def test_manifest_id_is_the_same_on_both_hosts():
    """Один и тот же ``id`` — единственное, что делает два манифеста одним приложением."""
    assert main.radar_manifest(True)["id"] == main.radar_manifest(False)["id"]


def test_manifest_icons_are_static_paths():
    for icon in main.radar_manifest(True)["icons"]:
        assert icon["src"].startswith("/static/radar/")


def test_root_manifest_route_only_on_radar_host():
    resp = asyncio.run(main.radar_root_manifest_route(_Request(RADAR_CANONICAL_HOST_DEFAULT)))
    assert resp.media_type == "application/manifest+json"
    assert b'"start_url":"/"' in resp.body.replace(b" ", b"")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.radar_root_manifest_route(_Request(_TECH_HOST)))
    assert exc.value.status_code == 404


def test_radar_manifest_route_is_host_independent():
    resp = asyncio.run(main.radar_manifest_route())
    assert resp.media_type == "application/manifest+json"
    assert b'"start_url":"/radar"' in resp.body.replace(b" ", b"")


# ─── контекст шаблона ───────────────────────────────────────────


def test_ctx_on_radar_host_points_manifest_and_login_to_root():
    ctx = main._radar_template_ctx(_Request(RADAR_CANONICAL_HOST_DEFAULT))
    assert ctx["manifest_url"] == "/manifest.webmanifest"
    assert ctx["home"] == "/"
    # Вход — через ЕСА, next на корень СВОЕГО хоста, а не на /radar.
    assert ctx["login_url"].startswith("https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai/login?next=")
    assert "%2Fradar" not in ctx["login_url"]
    assert RADAR_CANONICAL_HOST_DEFAULT in ctx["login_url"]


def test_ctx_on_tech_host_keeps_radar_prefix():
    ctx = main._radar_template_ctx(_Request(_TECH_HOST))
    assert ctx["manifest_url"] == "/radar/manifest.webmanifest"
    assert ctx["home"] == "/radar"
    assert ctx["login_url"].endswith("/login?next=%2Fradar")


def test_static_manifest_is_gone():
    """Два источника правды (статика и маршрут) разошлись бы молча."""
    assert not (main.BASE_DIR / "web" / "static" / "radar" / "manifest.webmanifest").exists()
