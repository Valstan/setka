"""Auth-ядро радара: пароли (stdlib scrypt) + stateless signed-cookie сессии.

Дизайн (Ф0.1, план mailbox/to-brain/2026-06-12-content-radar-f0-plan.md):

- **Пароли** — `hashlib.scrypt` (stdlib, без новых зависимостей на tiny-VPS).
  Формат хранения: ``scrypt$<n>$<r>$<p>$<salt_b64>$<hash_b64>`` — параметры в
  строке, так что их можно поднять в будущем без миграции (старые хэши
  продолжат проверяться по своим параметрам).

- **Сессии** — stateless HMAC-подписанный токен в httponly-cookie, без таблицы
  сессий: ``b64url(json payload).b64url(hmac_sha256(secret, payload))``.
  В payload входит ``pf`` — фрагмент bulletin'а password_hash: смена пароля
  автоматически инвалидирует все выданные сессии (middleware сверяет pf с
  актуальным хэшем из БД на каждом запросе — БД-lookup по PK, трафик мал).

- **Секрет** — env ``SETKA_WEB_SECRET`` (#008, /etc/setka/setka.env). Если не
  задан — ephemeral на процесс (сессии живут до рестарта) + громкий WARNING:
  это деградация для dev, на проде секрет обязателен.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

SESSION_COOKIE = "setka_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # 30 дней

# scrypt-параметры по умолчанию (новые хэши). Подобраны под tiny-VPS (1.5 GB):
# n=2^14, r=8, p=1 ≈ 16 MB памяти на проверку — заметная цена брутфорсу,
# незаметная единственному оператору.
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32

_ephemeral_secret: Optional[bytes] = None


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def _secret() -> bytes:
    """Ключ подписи сессий: env SETKA_WEB_SECRET либо ephemeral + WARNING."""
    env = os.getenv("SETKA_WEB_SECRET")
    if env:
        return env.encode()
    global _ephemeral_secret
    if _ephemeral_secret is None:
        _ephemeral_secret = secrets.token_bytes(32)
        logger.warning(
            "SETKA_WEB_SECRET is not set — using an ephemeral session secret; "
            "all sessions will be invalidated on restart. Set it in /etc/setka/setka.env."
        )
    return _ephemeral_secret


# ---------------------------------------------------------------------------
# Пароли
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    """scrypt-хэш пароля в self-describing формате (параметры в строке)."""
    salt = secrets.token_bytes(16)
    bulletin = hashlib.scrypt(
        password.encode(),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_b64e(salt)}${_b64e(bulletin)}"


def verify_password(password: str, stored: str) -> bool:
    """Проверка пароля против хранимого хэша; любой мусор в stored → False."""
    try:
        algo, n, r, p, salt_b64, hash_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        expected = _b64d(hash_b64)
        actual = hashlib.scrypt(
            password.encode(),
            salt=_b64d(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def password_fragment(password_hash: str) -> str:
    """Короткий fingerprint хэша пароля для инвалидации сессий при его смене."""
    return hashlib.sha256(password_hash.encode()).hexdigest()[:12]


# ---------------------------------------------------------------------------
# Сессионные токены
# ---------------------------------------------------------------------------


def issue_session_token(
    user_id: int,
    role: str,
    pwd_fragment: str,
    ttl_seconds: int = SESSION_TTL_SECONDS,
    _now: Optional[float] = None,
) -> str:
    """Подписанный токен сессии: payload.signature (оба — b64url).

    ``iat`` — момент, когда человек РЕАЛЬНО аутентифицировался; из него ЕСА
    берёт `auth_time` в ID-токене (шаг 1 порядка, согласованного с мозгом
    15.09). До 18.09 в payload его не было вовсе, и `auth_time` подставлялся
    из «сейчас» — то есть ЕСА утверждал время входа, которого не знал.
    Старая cookie без ``iat`` остаётся валидной: `auth_time` тогда просто
    **не заявляется** (молчать честнее, чем угадывать).
    """
    now = time.time() if _now is None else _now
    payload = {
        "uid": user_id,
        "role": role,
        "pf": pwd_fragment,
        "iat": int(now),
        "exp": int(now + ttl_seconds),
    }
    payload_b64 = _b64e(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest()
    return f"{payload_b64}.{_b64e(sig)}"


def verify_session_token(token: str, _now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Payload токена, если подпись валидна и не истёк; иначе None."""
    try:
        payload_b64, sig_b64 = token.split(".")
        expected = hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        payload = json.loads(_b64d(payload_b64))
        now = time.time() if _now is None else _now
        if int(payload.get("exp", 0)) < now:
            return None
        return payload
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Метка «этот запрос уже отправлял переспросить вход» (OIDC prompt=login)
# ---------------------------------------------------------------------------
#
# Зачем она вообще. `prompt=login` означает «пусть человек войдёт ЗАНОВО,
# в рамках этого запроса». Живая сессия требование не выполняет, поэтому
# authorize уводит на /login — и на возврате должен отличить «вошёл только
# что» от «пришёл с той же старой сессией». Без точки отсчёта получается
# петля: старая сессия снова не годится, снова редирект, и так навсегда.
#
# Точка отсчёта едет в самом URL, поэтому она ПОДПИСАНА: без подписи любой
# желающий поставил бы `_reauth=0` и обошёл требование клиента, ничего не
# вводя. Секрет — тот же, что у сессионной куки; метка коротко живёт и ничего
# не значит сама по себе (в ней только момент времени).

REAUTH_MARKER_TTL_SECONDS = 15 * 60


def issue_reauth_marker(_now: Optional[float] = None) -> str:
    """Подписанная метка «переспрашиваем вход, отсчёт отсюда»."""
    now = int(time.time() if _now is None else _now)
    sig = hmac.new(_secret(), str(now).encode(), hashlib.sha256).digest()
    return f"{now}.{_b64e(sig)}"


def read_reauth_marker(raw: Optional[str], _now: Optional[float] = None) -> Optional[float]:
    """Момент из метки, если подпись валидна и метка не протухла; иначе None.

    ``None`` читается вызывающим как «метки нет» — то есть требование
    `prompt=login` ещё не выполнено. Протухшую метку (человек ушёл пить чай)
    отбрасываем так же: лучше переспросить второй раз, чем засчитать вход,
    случившийся неизвестно когда.
    """
    if not raw:
        return None
    try:
        ts_raw, sig_b64 = raw.split(".")
        expected = hmac.new(_secret(), ts_raw.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64d(sig_b64)):
            return None
        ts = float(int(ts_raw))
    except (ValueError, TypeError):
        return None
    now = time.time() if _now is None else _now
    if ts > now + 60 or ts < now - REAUTH_MARKER_TTL_SECONDS:
        return None
    return ts
