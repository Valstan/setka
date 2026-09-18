"""`auth_time` в ЕСА говорит о входе, а не о выдаче токена.

Разбор 2026-09-14 и директива мозга 15.09 (`recommend`, порядок согласован:
сперва честный `auth_time` из `iat` сессии, `prompt`/`max_age` — следом).

Было два независимых вранья, и второе дороже первого:

1. `issue_auth_code` ставил `auth_time = now` — момент **выдачи кода** выдавался
   за момент входа. Ошибка ограничена возрастом кода (секунды), но она
   систематическая: время входа не участвовало в ответе вообще;
2. `refresh_grant` брал `auth_time=_utcnow()` — то есть **каждое обновление
   токена** утверждало, что человек аутентифицировался только что. Сессия
   недельной давности выглядела свежей ровно настолько, насколько часто клиент
   обновлялся. Именно этот claim читает клиент, решая, переспрашивать ли пароль.

Гвоздь файла — `test_refresh_does_not_bump_auth_time` и
`test_unknown_auth_time_is_absent_not_now`: первый ловит возврат вранья №2,
второй держит правило «молчать честнее, чем угадывать» — сессия без `iat`
(cookie старше 18.09) обязана дать ОТСУТСТВУЮЩИЙ claim, а не «сейчас».
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta

import pytest
from authlib.jose import jwt
from authlib.oauth2.rfc7636 import create_s256_code_challenge

from database.models_extended import OAuthClient, RadarUser
from modules.radar.auth import hash_password, issue_session_token, verify_session_token
from modules.radar_id import service
from modules.radar_id.keys import get_public_jwks

REDIRECT = "https://client.test/auth/callback"
VERIFIER = "b" * 48
CHALLENGE = create_s256_code_challenge(VERIFIER)


async def _seed(db_session):
    client = OAuthClient(
        client_id="kazanskaya",
        client_secret_hash=hash_password("s3cret"),
        name="Казанская ярмарка",
        redirect_uris=[REDIRECT],
        allowed_scopes="openid profile email",
        is_confidential=True,
    )
    user = RadarUser(
        login="visitor",
        password_hash=hash_password("pw"),
        role="radar",
        email="visitor@example.test",
        email_verified=True,
        display_name="Посетитель",
    )
    db_session.add_all([client, user])
    await db_session.commit()
    return client, user


async def _code(db_session, client, user, auth_time):
    return await service.issue_auth_code(
        db_session,
        client=client,
        user=user,
        redirect_uri=REDIRECT,
        scope="openid profile email",
        code_challenge=CHALLENGE,
        code_challenge_method="S256",
        nonce="n0nce",
        auth_time=auth_time,
    )


def _claims(id_token):
    return jwt.decode(id_token, get_public_jwks())


class TestSessionCarriesLoginTime:
    def test_session_token_carries_iat(self):
        """Шаг 1: без `iat` в сессии брать время входа неоткуда."""
        token = issue_session_token(7, "radar", "frag", _now=1_700_000_000.0)
        payload = verify_session_token(token, _now=1_700_000_000.0)
        assert payload is not None
        assert payload["iat"] == 1_700_000_000

    def test_old_cookie_without_iat_still_valid(self):
        """Совместимость: сессии, выданные до 18.09, не разлогинивает."""
        token = issue_session_token(7, "radar", "frag")
        payload = verify_session_token(token)
        assert payload is not None and payload["uid"] == 7


class TestIdTokenAuthTime:
    @pytest.mark.asyncio
    async def test_auth_time_is_login_time_not_code_time(self, db_session, rsa_key_env):
        """Вход был вчера — в токене вчерашнее время, а не «сейчас»."""
        client, user = await _seed(db_session)
        logged_in = datetime.utcnow() - timedelta(days=1)
        raw = await _code(db_session, client, user, logged_in)

        bundle = await service.exchange_code(
            db_session,
            client=client,
            raw_code=raw,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
        )
        claims = _claims(bundle.id_token)
        assert claims["auth_time"] == int(logged_in.timestamp())
        # И это заметно отличается от момента выдачи токена.
        assert int(time.time()) - claims["auth_time"] > 3600

    @pytest.mark.asyncio
    async def test_unknown_auth_time_is_absent_not_now(self, db_session, rsa_key_env):
        """Молчать честнее, чем угадывать: нет `iat` → нет claim'а."""
        client, user = await _seed(db_session)
        raw = await _code(db_session, client, user, None)

        bundle = await service.exchange_code(
            db_session,
            client=client,
            raw_code=raw,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
        )
        claims = _claims(bundle.id_token)
        assert "auth_time" not in claims
        # Остальные обязательные claims на месте — молчим только про время входа.
        assert claims["sub"] == user.sub and claims["aud"] == client.client_id

    @pytest.mark.asyncio
    async def test_refresh_does_not_bump_auth_time(self, db_session, rsa_key_env):
        """Гвоздь: обновление токена — не аутентификация.

        Здесь стоял `_utcnow()`, и каждый refresh сдвигал время входа на
        «сейчас». Клиент с `max_age` не переспросил бы пароль никогда.
        """
        client, user = await _seed(db_session)
        logged_in = datetime.utcnow() - timedelta(days=3)
        raw = await _code(db_session, client, user, logged_in)

        first = await service.exchange_code(
            db_session,
            client=client,
            raw_code=raw,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
        )
        second = await service.refresh_grant(
            db_session, client=client, raw_refresh=first.refresh_token
        )
        third = await service.refresh_grant(
            db_session, client=client, raw_refresh=second.refresh_token
        )

        expected = int(logged_in.timestamp())
        assert _claims(second.id_token)["auth_time"] == expected
        # Вторая ротация тоже — значение едет по цепочке, а не по первому шагу.
        assert _claims(third.id_token)["auth_time"] == expected

    @pytest.mark.asyncio
    async def test_refresh_keeps_silence_when_login_time_unknown(self, db_session, rsa_key_env):
        """NULL по цепочке остаётся NULL, а не превращается в «сейчас»."""
        client, user = await _seed(db_session)
        raw = await _code(db_session, client, user, None)

        first = await service.exchange_code(
            db_session,
            client=client,
            raw_code=raw,
            redirect_uri=REDIRECT,
            code_verifier=VERIFIER,
        )
        second = await service.refresh_grant(
            db_session, client=client, raw_refresh=first.refresh_token
        )
        assert "auth_time" not in _claims(second.id_token)
