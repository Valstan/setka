"""Unit tests для ``scripts/register_oidc_client.py`` — брендинг OIDC-клиента.

Регистрация клиента ЕСА идёт разово и руками на проде, поэтому цена ошибки
несимметрична: перезапустить скрипт дёшево, а вот молча стереть карточку
сервиса на странице входа (или подставить в стиль кривой цвет) заметит уже
посетитель. Покрываем ровно эти две границы:

* ``merge_branding`` — «не передали флаг» обязано отличаться от «передали
  пустое». Повторный прогон ради нового ``redirect_uri`` не должен обнулять
  брендинг, а частичная правка (только эмодзи) — сносить заголовок и подпись.
* ``_accent`` — цвет валидируется на входе, потому что дальше обработка
  fail-open (``modules/radar_id/branding.py``): кривое значение не упадёт, а
  тихо отрисуется дефолтом, и понять это можно только глазами на живой странице.

БД не нужна — обе функции чистые. Скрипт вне устанавливаемого пакета, грузим
через importlib (как ``test_activate_regions.py``).
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "register_oidc_client", REPO_ROOT / "scripts" / "register_oidc_client.py"
)
register_oidc_client = importlib.util.module_from_spec(_spec)
sys.modules["register_oidc_client"] = register_oidc_client
_spec.loader.exec_module(register_oidc_client)

merge_branding = register_oidc_client.merge_branding

BASE_ARGV = [
    "--client-id",
    "sabantuy",
    "--name",
    "Сабантуй в Малмыже",
    "--redirect-uri",
    "https://example.test/api/auth/esa/callback",
]

SABANTUY_BRAND = {
    "title": "Сабантуй в Малмыже",
    "icon": "🌷",
    "accent": "#1f7a4d",
    "sub": "Программа праздника, фотостена и народная лента",
}


class TestMergeBranding:
    def test_no_flags_keeps_existing_untouched(self):
        """Перезапуск ради нового redirect_uri не должен стирать карточку."""
        assert merge_branding(SABANTUY_BRAND, {}) == SABANTUY_BRAND

    def test_no_flags_on_unbranded_client_stays_none(self):
        assert merge_branding(None, {}) is None

    def test_partial_update_preserves_other_keys(self):
        result = merge_branding(SABANTUY_BRAND, {"icon": "🎪"})
        assert result["icon"] == "🎪"
        assert result["title"] == SABANTUY_BRAND["title"]
        assert result["sub"] == SABANTUY_BRAND["sub"]
        assert result["accent"] == SABANTUY_BRAND["accent"]

    def test_does_not_mutate_the_row_value_in_place(self):
        """SQLAlchemy отслеживает подмену JSON-поля, а не правку на месте."""
        existing = dict(SABANTUY_BRAND)
        merge_branding(existing, {"icon": "🎪"})
        assert existing["icon"] == "🌷"

    def test_full_brand_on_fresh_client(self):
        assert merge_branding(None, SABANTUY_BRAND) == SABANTUY_BRAND


class TestAccentValidation:
    @pytest.mark.parametrize("value", ["#1f7a4d", "#FFFFFF", "#0d6efd"])
    def test_accepts_six_digit_hex(self, value):
        assert register_oidc_client._accent(value) == value

    @pytest.mark.parametrize(
        "value",
        ["1f7a4d", "#1f7a4", "#1f7a4dd", "green", "#1f7a4z", "", "#1f7a4d; color:red"],
    )
    def test_rejects_anything_else(self, value):
        with pytest.raises(argparse.ArgumentTypeError):
            register_oidc_client._accent(value)


class TestGivenBranding:
    def test_empty_when_no_brand_flags(self):
        args = register_oidc_client._parse_args(BASE_ARGV)
        assert register_oidc_client._given_branding(args) == {}

    def test_collects_only_passed_flags(self):
        args = register_oidc_client._parse_args(BASE_ARGV + ["--brand-icon", "🌷"])
        assert register_oidc_client._given_branding(args) == {"icon": "🌷"}

    def test_collects_full_card(self):
        args = register_oidc_client._parse_args(
            BASE_ARGV
            + [
                "--brand-title",
                SABANTUY_BRAND["title"],
                "--brand-icon",
                SABANTUY_BRAND["icon"],
                "--brand-accent",
                SABANTUY_BRAND["accent"],
                "--brand-sub",
                SABANTUY_BRAND["sub"],
            ]
        )
        assert register_oidc_client._given_branding(args) == SABANTUY_BRAND

    def test_bad_accent_fails_before_touching_the_database(self):
        with pytest.raises(SystemExit):
            register_oidc_client._parse_args(BASE_ARGV + ["--brand-accent", "зелёный"])


class TestArgDefaults:
    def test_scopes_default_includes_email(self):
        args = register_oidc_client._parse_args(BASE_ARGV)
        assert args.scopes == "openid profile email"

    def test_scopes_can_drop_email(self):
        """Сабантуй просит openid profile — email не запрашивают (152-ФЗ)."""
        args = register_oidc_client._parse_args(BASE_ARGV + ["--scopes", "openid profile"])
        assert args.scopes == "openid profile"

    def test_redirect_uri_is_repeatable(self):
        args = register_oidc_client._parse_args(
            BASE_ARGV + ["--redirect-uri", "http://localhost:3000/api/auth/esa/callback"]
        )
        assert len(args.redirect_uri) == 2
