"""Валидация self-serve выдачи (:mod:`modules.ecosystem.provisioning`).

Цена ошибки здесь несимметричная. Заявку присылает **сервер чужого проекта**,
без человека в цикле, поэтому всё, что валидатор пропустит, окажется в проде
молча: кривой ``redirect_uri`` — это либо сломанный вход у соседа, либо
(в худшем случае) чужой адрес возврата; кривой цвет — тихо испорченная
страница входа; лишний scope — выданные claims сверх нужного (152-ФЗ).

БД не нужна — все проверяемые функции чистые.
"""

from __future__ import annotations

import pytest

from modules.ecosystem.provisioning import (
    ProvisioningError,
    merge_branding,
    validate_branding,
    validate_client_id,
    validate_name,
    validate_project_name,
    validate_redirect_uris,
    validate_scopes,
)

PORTAL_URI = "https://xn--80adkdyec4j.xn--p1ai/auth/esa/callback"


class TestClientId:
    @pytest.mark.parametrize("value", ["sabantuy", "portal-vmalmyzhe", "trener_2", "ab"])
    def test_accepts_slug(self, value):
        assert validate_client_id(value) == value

    @pytest.mark.parametrize(
        "value",
        ["", "a", "1portal", "Portal", "порталь", "with space", "-dash", "x" * 64],
    )
    def test_rejects_everything_else(self, value):
        with pytest.raises(ProvisioningError) as e:
            validate_client_id(value)
        assert e.value.code == "invalid_client_id"

    def test_strips_whitespace(self):
        assert validate_client_id("  portal  ") == "portal"


class TestName:
    def test_accepts_human_name(self):
        assert validate_name(" Портал вМалмыже ") == "Портал вМалмыже"

    @pytest.mark.parametrize("value", ["", "   ", "x" * 129])
    def test_rejects_empty_or_too_long(self, value):
        with pytest.raises(ProvisioningError):
            validate_name(value)


class TestRedirectUris:
    def test_accepts_https_punycode(self):
        assert validate_redirect_uris([PORTAL_URI]) == [PORTAL_URI]

    def test_accepts_http_on_localhost_for_dev(self):
        uri = "http://localhost:3000/auth/esa/callback"
        assert validate_redirect_uris([uri]) == [uri]

    def test_rejects_http_on_public_host(self):
        with pytest.raises(ProvisioningError) as e:
            validate_redirect_uris(["http://example.test/cb"])
        assert e.value.code == "invalid_redirect_uri"

    def test_rejects_cyrillic_host_with_punycode_hint(self):
        """G108: .рф в OAuth-полях сравнивается символ-в-символ."""
        with pytest.raises(ProvisioningError) as e:
            validate_redirect_uris(["https://вмалмыже.рф/auth/esa/callback"])
        assert "punycode" in e.value.message

    def test_rejects_fragment(self):
        with pytest.raises(ProvisioningError):
            validate_redirect_uris(["https://example.test/cb#frag"])

    def test_rejects_relative(self):
        with pytest.raises(ProvisioningError):
            validate_redirect_uris(["/auth/esa/callback"])

    def test_rejects_empty_list(self):
        with pytest.raises(ProvisioningError):
            validate_redirect_uris([])

    def test_rejects_too_many(self):
        with pytest.raises(ProvisioningError):
            validate_redirect_uris([f"https://example.test/cb{i}" for i in range(6)])

    def test_deduplicates_preserving_order(self):
        second = "https://example.test/other"
        assert validate_redirect_uris([PORTAL_URI, second, PORTAL_URI]) == [PORTAL_URI, second]


class TestScopes:
    def test_default_is_openid_profile(self):
        assert validate_scopes(None) == "openid profile"

    def test_canonical_order(self):
        assert validate_scopes("email openid profile") == "openid profile email"

    def test_requires_openid(self):
        with pytest.raises(ProvisioningError) as e:
            validate_scopes("profile email")
        assert e.value.code == "invalid_scope"

    def test_rejects_unknown_scope(self):
        """Потолок claims — иначе клиент попросил бы то, чего мы не отдаём."""
        with pytest.raises(ProvisioningError) as e:
            validate_scopes("openid admin")
        assert "admin" in e.value.message


class TestBranding:
    def test_empty_means_do_not_touch(self):
        assert validate_branding(None) == {}
        assert validate_branding({}) == {}

    def test_accepts_full_card(self):
        card = {
            "title": "Портал вМалмыже",
            "icon": "🏛",
            "accent": "#1f7a4d",
            "sub": "Новости и услуги района",
        }
        assert validate_branding(card) == card

    def test_rejects_unknown_key(self):
        with pytest.raises(ProvisioningError) as e:
            validate_branding({"colour": "red"})
        assert e.value.code == "invalid_branding"

    @pytest.mark.parametrize("value", ["red", "#1f7a4", "#1f7a4dd", "#1f7a4d; color:red"])
    def test_rejects_bad_accent(self, value):
        """Обработка брендинга fail-open — кривой цвет заметит только посетитель."""
        with pytest.raises(ProvisioningError):
            validate_branding({"accent": value})

    def test_rejects_oversized_icon_and_title(self):
        with pytest.raises(ProvisioningError):
            validate_branding({"icon": "🏛" * 9})
        with pytest.raises(ProvisioningError):
            validate_branding({"title": "x" * 129})


class TestMergeBranding:
    CARD = {"title": "Сабантуй", "icon": "🌷", "accent": "#1f7a4d", "sub": "Программа"}

    def test_no_given_keeps_existing(self):
        assert merge_branding(self.CARD, {}) == self.CARD
        assert merge_branding(None, {}) is None

    def test_partial_update_preserves_rest(self):
        result = merge_branding(self.CARD, {"icon": "🎪"})
        assert result["icon"] == "🎪"
        assert result["title"] == "Сабантуй"

    def test_does_not_mutate_in_place(self):
        """SQLAlchemy видит подмену JSON-поля, а не правку на месте."""
        existing = dict(self.CARD)
        merge_branding(existing, {"icon": "🎪"})
        assert existing["icon"] == "🌷"


class TestProjectName:
    def test_uppercases(self):
        assert validate_project_name("kazanskaya") == "KAZANSKAYA"

    @pytest.mark.parametrize("value", ["", "K", "1KEY", "КАЗАНСКАЯ", "with space", "X" * 51])
    def test_rejects_everything_else(self, value):
        with pytest.raises(ProvisioningError) as e:
            validate_project_name(value)
        assert e.value.code == "invalid_project"
