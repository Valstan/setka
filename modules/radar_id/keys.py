"""RS256-ключи Радар-ID: загрузка приватного PEM, JWKS, kid.

Крипта — библиотекой Authlib/joserfc (канон: auth не хэндроллим).
Приватный ключ живёт файлом вне репо (``RADAR_ID_PRIVATE_KEY_FILE``,
дефолт ``/etc/setka/radar_id_rs256.pem``); генерация —
``scripts/generate_radar_id_key.py``.

``kid`` = RFC 7638 thumbprint публичного ключа — стабилен для данного
ключа, меняется при ротации: клиенты с офлайн-JWKS (MUST ADR-0002 §5.1)
матчат подпись по kid и дотягивают новый JWKS при незнакомом kid.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Dict

from authlib.jose import JsonWebKey

from config.radar_id import get_private_key_file

logger = logging.getLogger(__name__)


class RadarIdKeyError(RuntimeError):
    """Приватный ключ подписи недоступен/невалиден."""


@lru_cache(maxsize=1)
def _load_private_jwk():
    """Прочитать приватный PEM и вернуть authlib JsonWebKey (RSA).

    lru_cache: файл читается один раз на процесс. Ротация ключа = замена
    файла + рестарт сервисов (осознанно: ротация и так требует выкладки
    нового JWKS клиентам заранее).
    """
    path = get_private_key_file()
    try:
        with open(path, "rb") as f:
            pem = f.read()
    except OSError as e:
        raise RadarIdKeyError(f"cannot read private key file {path}: {e}") from e
    try:
        key = JsonWebKey.import_key(pem, {"kty": "RSA", "use": "sig", "alg": "RS256"})
    except Exception as e:  # authlib кидает разные типы на кривом PEM
        raise RadarIdKeyError(f"invalid RS256 private key in {path}: {e}") from e
    return key


def get_signing_key():
    """Приватный JWK для подписи id_token/access (RS256)."""
    return _load_private_jwk()


def get_kid() -> str:
    """kid ключа: RFC 7638 thumbprint (детерминирован по ключу)."""
    key = _load_private_jwk()
    return key.kid or key.thumbprint()


def get_public_jwks() -> Dict[str, Any]:
    """Публичный JWKS-документ для ``/.well-known/jwks.json``.

    Только публичная часть (as_dict(private=False)); клиенты кэшируют его
    для офлайн-валидации id_token (MUST §5.1).
    """
    key = _load_private_jwk()
    pub = key.as_dict(is_private=False)
    pub.setdefault("kid", get_kid())
    pub.setdefault("use", "sig")
    pub.setdefault("alg", "RS256")
    return {"keys": [pub]}


def keys_available() -> bool:
    """True, если приватный ключ читается и валиден (для health/#018)."""
    try:
        _load_private_jwk()
        return True
    except RadarIdKeyError as e:
        logger.warning("radar-id: signing key unavailable: %s", e)
        return False
