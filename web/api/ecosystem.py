"""Self-serve подключение проектов экосистемы (``/api/ecosystem``, ADR-0010).

Мандат brain 2026-07-28 (решение владельца): внутри экосистемы стандартные
подключения делаются **без человека в цикле**. Проект-клиент присылает заявку
сам, аутентифицируясь экосистемным ключом (``X-Ecosystem-Key``, env
``ECOSYSTEM_KEY``, зеркало — vault-комната КАРМАНа), и получает:

* ``POST /api/ecosystem/oidc-clients`` — ``client_id``/``client_secret`` ЕСА
  (единый вход вход.вмалмыже.рф) плюс карточку сервиса на странице входа;
* ``POST /api/ecosystem/gateway-keys`` — ключ VK-шлюза (``/api/gateway``).

Оба канала — одна процедура и один ключ: два разных механизма развели бы
конфигурацию потребителей на ровном месте.

Почему это не расширяет поверхность атаки:

* **ключ выдачи один и он секретный** — попал он только к проектам экосистемы
  (grant в vault), а не в публичный доступ; утёк — гасим
  ``ECOSYSTEM_PROVISIONING_DISABLED=1``, уже выданное продолжает работать;
* **нагрузка ограничена ключом, а не процедурой** — квоты шлюза висят на
  выданном ключе (``GATEWAY_QUOTA_PER_*`` + общий бюджет), поэтому
  самообслуживание не меняет потолок расхода VK-токенов;
* **чужое не тронуть** — существующая запись правится только предъявлением её
  текущего секрета (иначе 409/403), см. :mod:`modules.ecosystem.provisioning`.

Auth-гейт приложения (session-cookie) сюда не лезет: ``/api/ecosystem/`` в
``PUBLIC_PREFIXES`` (middleware/auth_gate.py) — у эндпоинта своя key-защита,
как у ``/api/gateway/`` и ``/api/classifier/``.
"""

from __future__ import annotations

import hmac
import logging
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from config.ecosystem import get_ecosystem_key, provisioning_disabled
from modules.ecosystem.provisioning import (
    ProvisioningError,
    ProvisionResult,
    provision_gateway_key,
    provision_oidc_client,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# Куда идти проверять круг сразу после регистрации (ответ на вопрос brain
# 2026-07-28: смоук проходится четырьмя curl, без браузера и VK-аккаунта).
SMOKE_GUIDE = "docs/ops/esa-smoke-without-browser.md"
ESA_ISSUER = "https://xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai"


async def require_ecosystem_key(
    x_ecosystem_key: Optional[str] = Header(default=None, alias="X-Ecosystem-Key"),
) -> str:
    """Проверить экосистемный ключ выдачи.

    Fail-closed: ключ не настроен на проде → 503, а не «пускаем всех». Секрет
    в логи не попадает, сравнение constant-time.
    """
    if provisioning_disabled():
        raise HTTPException(status_code=503, detail="ecosystem provisioning disabled")
    expected = get_ecosystem_key()
    if not expected:
        logger.error("ecosystem: ECOSYSTEM_KEY не задан — выдача недоступна")
        raise HTTPException(status_code=503, detail="ecosystem provisioning is not configured")
    if not x_ecosystem_key:
        raise HTTPException(status_code=401, detail="Missing X-Ecosystem-Key")
    if not hmac.compare_digest(x_ecosystem_key, expected):
        logger.warning("ecosystem: rejected provisioning request with unknown key")
        raise HTTPException(status_code=401, detail="Unknown ecosystem key")
    return "ecosystem"


class OidcClientIn(BaseModel):
    """Заявка на клиента ЕСА (узкий DCR — поля только те, что нам нужны)."""

    client_id: str = Field(..., description="латинский slug, он же в логах аудита")
    name: str = Field(..., description="человеческое имя сервиса")
    redirect_uris: List[str] = Field(..., description="точные uri; для .рф — punycode (G108)")
    scopes: Optional[str] = Field(
        default=None, description="потолок scope, дефолт 'openid profile'"
    )
    public: bool = Field(default=False, description="public PKCE-клиент (без secret)")
    branding: Optional[Dict[str, str]] = Field(
        default=None, description="карточка на /login: title, icon, accent, sub"
    )
    rotate_secret: bool = Field(default=False, description="перевыпустить секрет своего клиента")
    current_secret: Optional[str] = Field(
        default=None, description="текущий client_secret — нужен, чтобы править свою же запись"
    )


class GatewayKeyIn(BaseModel):
    """Заявка на ключ VK-шлюза."""

    project: str = Field(..., description="имя проекта, напр. KAZANSKAYA")
    note: Optional[str] = Field(default=None, description="кто/зачем — видно оператору")
    rotate: bool = Field(default=False, description="перевыпустить свой ключ")
    current_secret: Optional[str] = Field(
        default=None, description="текущий ключ — нужен для ротации"
    )


def _payload(result: ProvisionResult, extra: Dict[str, object]) -> Dict[str, object]:
    body: Dict[str, object] = {"ok": True, "action": result.action, **extra}
    body.update(result.details)
    if result.secret:
        body["secret_shown_once"] = True
    return body


@router.post("/oidc-clients", summary="Зарегистрировать клиента ЕСА (self-serve)")
async def register_client(body: OidcClientIn, _: str = Depends(require_ecosystem_key)):
    """Создать клиента ЕСА или обновить свой (по предъявлении client_secret).

    ``client_secret`` возвращается ОДИН раз — в БД лежит только scrypt-hash,
    восстановить его нельзя, можно лишь перевыпустить (``rotate_secret``).
    """
    try:
        result = await provision_oidc_client(
            client_id=body.client_id,
            name=body.name,
            redirect_uris=body.redirect_uris,
            scopes=body.scopes,
            public=body.public,
            branding=body.branding,
            rotate_secret=body.rotate_secret,
            current_secret=body.current_secret,
        )
    except ProvisioningError as e:
        raise HTTPException(status_code=e.status, detail={"error": e.code, "message": e.message})

    payload = _payload(
        result,
        {
            "client_id": result.identifier,
            "issuer": ESA_ISSUER,
            "discovery": f"{ESA_ISSUER}/.well-known/openid-configuration",
            "smoke_guide": SMOKE_GUIDE,
        },
    )
    if result.secret:
        payload["client_secret"] = result.secret
    return payload


@router.post("/gateway-keys", summary="Получить ключ VK-шлюза (self-serve)")
async def issue_gateway_key(body: GatewayKeyIn, _: str = Depends(require_ecosystem_key)):
    """Выдать ключ шлюза проекту или ротировать свой (по предъявлении ключа)."""
    try:
        result = await provision_gateway_key(
            project=body.project,
            note=body.note,
            rotate=body.rotate,
            current_secret=body.current_secret,
        )
    except ProvisioningError as e:
        raise HTTPException(status_code=e.status, detail={"error": e.code, "message": e.message})

    payload = _payload(
        result,
        {
            "project": result.identifier,
            "endpoint": "/api/gateway/call",
            "header": "X-API-Key",
        },
    )
    if result.secret:
        payload["api_key"] = result.secret
    return payload
