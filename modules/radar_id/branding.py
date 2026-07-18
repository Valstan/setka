"""Брендинг страницы единого входа (вход.вмалмыже.рф).

Страница /login понимает, ОТКУДА пришёл посетитель, и показывает
«Войти в <Сервис>» со значком и акцентным цветом сервиса:

- OIDC-редирект (``next=/oidc/authorize?...client_id=X``) → брендинг
  клиента из БД (``oauth_clients.branding``, миграция 072; NULL → name);
- заход с сервисного поддомена самой СЕТКИ (радар.вмалмыже.рф) → Радар;
- иначе — нейтральный «Сервисы Малмыжа».

Брендинг — чисто косметика: никакие права/скоупы от него не зависят,
поэтому обработка максимально fail-open (не распознали → дефолт).
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

# Дефолт: нейтральный вход экосистемы.
DEFAULT_BRAND = {
    "title": "Сервисы Малмыжа",
    "icon": "🌿",
    "accent": "#0d6efd",
    "sub": "Один аккаунт — все сервисы района",
}

# Заход напрямую с сервисных поддоменов СЕТКИ (punycode host → брендинг).
HOST_BRANDS = {
    # радар.вмалмыже.рф
    "xn--80aal0cd.xn--80adkdyec4j.xn--p1ai": {
        "title": "Радар",
        "icon": "📡",
        "accent": "#0d6efd",
        "sub": "Ваша лента событий района",
    },
}


def _client_id_from_next(next_url: Optional[str]) -> Optional[str]:
    """Достать client_id из next=/oidc/authorize?...client_id=X (иначе None)."""
    if not next_url:
        return None
    try:
        parts = urlsplit(next_url)
        # Только свой относительный OIDC-путь — чужие/абсолютные url не разбираем.
        if parts.scheme or parts.netloc or parts.path != "/oidc/authorize":
            return None
        values = parse_qs(parts.query).get("client_id") or []
        return values[0] if values else None
    except (ValueError, TypeError):
        return None


async def resolve_brand(next_url: Optional[str], host: Optional[str]) -> dict:
    """Брендинг страницы входа: OIDC-клиент → поддомен → дефолт."""
    client_id = _client_id_from_next(next_url)
    if client_id:
        try:
            from sqlalchemy import select

            from database.models_extended import OAuthClient
            from database.connection import AsyncSessionLocal

            async with AsyncSessionLocal() as session:
                client = (
                    await session.execute(
                        select(OAuthClient).where(
                            OAuthClient.client_id == client_id,
                            OAuthClient.is_active.is_(True),
                        )
                    )
                ).scalar_one_or_none()
            if client is not None:
                brand = dict(DEFAULT_BRAND)
                brand["title"] = client.name
                brand.update(client.branding or {})
                return brand
        except Exception as e:  # noqa: BLE001 - косметика, не роняем логин
            logger.warning("login branding lookup failed for %r: %s", client_id, e)

    host_brand = HOST_BRANDS.get((host or "").split(":")[0].lower())
    if host_brand:
        return {**DEFAULT_BRAND, **host_brand}
    return dict(DEFAULT_BRAND)
