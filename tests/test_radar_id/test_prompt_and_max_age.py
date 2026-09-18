"""`prompt` и `max_age`: клиент просит свежий вход — ЕСА обязан ответить честно.

Шаг 3 порядка, согласованного с мозгом 15.09 (после честного `auth_time`).
Смысл параметров один: «вход должен быть новее X». Разница в том, что делать,
когда он старее, и здесь легко сделать три ошибки, каждая из которых выглядит
как работающая фича:

1. **Тихо проигнорировать.** Клиент просил переспросить пароль, получил код —
   и уверен, что человек только что подтвердил личность. Поэтому неизвестный
   `prompt`, `consent` и `select_account` (экранов у нас нет) дают ОТКАЗ, а не
   молчание;
2. **Зациклиться.** `prompt=login` не выполняется живой сессией; уводим на
   /login — а на возврате сессия всё ещё «не новая», и человек ходит по кругу.
   Точка отсчёта едет подписанной меткой в URL: тест
   `test_login_prompt_is_satisfied_by_login_after_marker` — это про выход из
   петли, `test_marker_signature_is_required` — про то, что метку нельзя
   подделать (иначе требование клиента обходится голыми руками);
3. **Показать UI при `prompt=none`.** Клиент прямо запретил интерфейс; редирект
   на форму входа для него — зависший iframe вместо ответа `login_required`.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest

from modules.radar.auth import issue_reauth_marker, read_reauth_marker
from modules.radar_id import service
from modules.radar_id.service import OidcError

NOW = datetime(2026, 9, 18, 12, 0, 0)


class TestParsePrompt:
    def test_empty_is_no_requirement(self):
        assert service.parse_prompt(None) == frozenset()
        assert service.parse_prompt("") == frozenset()

    def test_supported_values(self):
        assert service.parse_prompt("login") == frozenset({"login"})
        assert service.parse_prompt("none") == frozenset({"none"})

    def test_unknown_value_is_refused_not_ignored(self):
        """Молча съесть — значит соврать клиенту, что требование выполнено."""
        with pytest.raises(OidcError) as e:
            service.parse_prompt("logout")
        assert e.value.error == "invalid_request"

    def test_none_cannot_be_combined(self):
        """«Не показывай UI» и «покажи UI» вместе неисполнимы."""
        with pytest.raises(OidcError) as e:
            service.parse_prompt("none login")
        assert e.value.error == "invalid_request"

    def test_consent_gets_honest_refusal(self):
        """Consent-экрана нет — спек требует сказать это, а не выдать код."""
        with pytest.raises(OidcError) as e:
            service.parse_prompt("consent")
        assert e.value.error == "consent_required"

    def test_select_account_gets_honest_refusal(self):
        with pytest.raises(OidcError) as e:
            service.parse_prompt("select_account")
        assert e.value.error == "account_selection_required"


class TestParseMaxAge:
    def test_absent(self):
        assert service.parse_max_age(None) is None
        assert service.parse_max_age("") is None

    def test_seconds(self):
        assert service.parse_max_age("300") == 300
        assert service.parse_max_age("0") == 0

    @pytest.mark.parametrize("bad", ["-1", "abc", "5.5"])
    def test_garbage_is_invalid_request(self, bad):
        with pytest.raises(OidcError) as e:
            service.parse_max_age(bad)
        assert e.value.error == "invalid_request"


class TestNeedsReauth:
    def test_no_requirements_never_asks(self):
        assert not service.needs_reauth(prompt=frozenset(), max_age=None, auth_time=None, now=NOW)

    def test_max_age_satisfied_by_recent_login(self):
        assert not service.needs_reauth(
            prompt=frozenset(),
            max_age=600,
            auth_time=NOW - timedelta(minutes=5),
            now=NOW,
        )

    def test_max_age_violated_by_old_login(self):
        assert service.needs_reauth(
            prompt=frozenset(),
            max_age=600,
            auth_time=NOW - timedelta(hours=5),
            now=NOW,
        )

    def test_max_age_with_unknown_login_time_asks(self):
        """Клиент просил доказательство свежести — доказывать нечем."""
        assert service.needs_reauth(prompt=frozenset(), max_age=600, auth_time=None, now=NOW)

    def test_zero_max_age_always_asks(self):
        """`max_age=0` — «только что»; любая прошлая сессия не годится."""
        assert service.needs_reauth(
            prompt=frozenset(),
            max_age=0,
            auth_time=NOW - timedelta(seconds=1),
            now=NOW,
        )

    def test_login_prompt_is_not_satisfied_by_live_session(self):
        """Гвоздь: без этого `prompt=login` — украшение."""
        assert service.needs_reauth(
            prompt=frozenset({"login"}),
            max_age=None,
            auth_time=NOW - timedelta(seconds=30),
            now=NOW,
            reauth_since=None,
        )

    def test_login_prompt_is_satisfied_by_login_after_marker(self):
        """Выход из петли: вход СЛУЧИЛСЯ после того, как мы попросили."""
        asked_at = NOW - timedelta(seconds=40)
        assert not service.needs_reauth(
            prompt=frozenset({"login"}),
            max_age=None,
            auth_time=asked_at + timedelta(seconds=10),
            now=NOW,
            reauth_since=asked_at,
        )

    def test_login_before_marker_still_asks(self):
        """Вернулся с той же старой сессией — требование не выполнено."""
        asked_at = NOW - timedelta(seconds=40)
        assert service.needs_reauth(
            prompt=frozenset({"login"}),
            max_age=None,
            auth_time=asked_at - timedelta(minutes=5),
            now=NOW,
            reauth_since=asked_at,
        )

    def test_max_age_and_login_are_independent(self):
        """Свежий вход по метке, но старше max_age → всё равно переспрашиваем."""
        asked_at = NOW - timedelta(seconds=30)
        assert service.needs_reauth(
            prompt=frozenset({"login"}),
            max_age=10,
            auth_time=asked_at + timedelta(seconds=5),
            now=NOW,
            reauth_since=asked_at,
        )


class TestReauthMarker:
    @pytest.fixture(autouse=True)
    def _secret(self, monkeypatch):
        monkeypatch.setenv("SETKA_WEB_SECRET", "test-secret-key")

    def test_roundtrip(self):
        now = time.time()
        assert read_reauth_marker(issue_reauth_marker(_now=now), _now=now) == int(now)

    def test_marker_signature_is_required(self):
        """Подделка `_reauth` обошла бы требование клиента без единого ввода."""
        now = time.time()
        forged = f"{int(now) - 10}.{issue_reauth_marker(_now=now).split('.')[1]}"
        assert read_reauth_marker(forged, _now=now) is None
        assert read_reauth_marker("0.", _now=now) is None
        assert read_reauth_marker("garbage", _now=now) is None
        assert read_reauth_marker(None, _now=now) is None

    def test_stale_marker_is_rejected(self):
        """Ушёл пить чай — переспросим второй раз, это дешевле вранья."""
        issued = time.time()
        marker = issue_reauth_marker(_now=issued)
        assert read_reauth_marker(marker, _now=issued + 16 * 60) is None

    def test_marker_from_the_future_is_rejected(self):
        issued = time.time() + 600
        assert read_reauth_marker(issue_reauth_marker(_now=issued), _now=time.time()) is None


class TestDiscoveryAdvertisesOnlyWhatWeDo:
    def test_prompt_values_supported(self, rsa_key_env):
        doc = service.discovery_document()
        assert doc["prompt_values_supported"] == ["login", "none"]
        # Экранов нет — обещать их нельзя.
        assert "consent" not in doc["prompt_values_supported"]
        assert "select_account" not in doc["prompt_values_supported"]
