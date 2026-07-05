"""AuthGateMiddleware — app-level auth + изоляция ролей (Ф0.1 контент-радара).

Secure by default: ВСЁ приложение закрыто, кроме явного allowlist'а
(PUBLIC_PREFIXES). Новый операторский route защищён автоматически — забыть
повесить зависимость невозможно, enforcement живёт в одном месте.

Роли (директива brain 2026-06-11, решение владельца раунд 2 §1):
- ``operator`` — весь setka (регионы/CRM/токены/мониторинг/...).
- ``radar``    — только контент-радар (RADAR_PREFIXES) + auth-эндпоинты.
  Операторский setka для radar-юзера = 403.

Сессия — stateless signed-cookie (modules/radar/auth.py). На каждом запросе
юзер перечитывается из БД по PK: проверяем is_active и совпадение
password-fragment (смена пароля / деактивация инвалидирует сессию немедленно).

Неаутентифицированный запрос: браузерный GET (Accept: text/html) → 302 на
/login?next=..., API/прочее → 401 JSON. 403 — аутентифицирован, но не та роль.

Kill-switch: env ``WEB_AUTH_ENABLED=0`` отключает гейт целиком (локальный dev
без БД-юзеров). Дефолт — включено; на проде не выключать.

/metrics — особый случай: Prometheus скрейпит с localhost; снаружи метрики
не отдаём (оператору залогиненным — можно).
"""

from __future__ import annotations

import logging
import os
from typing import Awaitable, Callable, Optional
from urllib.parse import quote

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse

from modules.radar.auth import SESSION_COOKIE, password_fragment, verify_session_token

logger = logging.getLogger(__name__)

# Открыто без auth (префиксное сравнение):
PUBLIC_PREFIXES = (
    "/login",
    "/static/",
    "/favicon.ico",
    "/api/health",  # internal watchdogs/CI ходят на 127.0.0.1:8000 без cookie
    "/api/gateway/",  # VK-шлюз: своя X-API-Key защита (web/api/gateway.py).
    # trailing slash важен: /api/gateway-stats НЕ public — это операторская
    # статистика под сессионной cookie (web/api/gateway_stats.py).
    "/api/auth/login",
    "/api/auth/register",
    "/.well-known/",
    # Радар-ID OIDC (web/api/radar_id.py): token/userinfo зовут серверы
    # клиентов без cookie — своя client-auth (secret/PKCE/Bearer).
    # /oidc/authorize сюда НЕ входит — ему нужна сессия пользователя.
    "/oidc/token",
    "/oidc/userinfo",
)

# Куда пускаем роль radar (плюс PUBLIC сверху):
RADAR_PREFIXES = (
    "/radar",
    "/api/radar/",
    "/api/auth/logout",
    "/api/auth/me",
    "/oidc/",  # OIDC authorize: любой залогиненный RadarUser может входить на сайты
)

UserLoader = Callable[[int], Awaitable[Optional[object]]]


async def _default_user_loader(user_id: int):
    """Достать RadarUser по PK свежей сессией (импорт внутри — лёгкий старт тестов)."""
    from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
    from database.connection import AsyncSessionLocal
    from database.models_extended import RadarUser

    async with AsyncSessionLocal() as session:
        return await session.get(RadarUser, user_id)


def _is_prefixed(path: str, prefixes: tuple) -> bool:
    return any(path == p.rstrip("/") or path.startswith(p) for p in prefixes)


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


def _is_local_client(request: Request) -> bool:
    client = request.client
    return bool(client and client.host in ("127.0.0.1", "::1"))


class AuthGateMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, user_loader: Optional[UserLoader] = None):
        super().__init__(app)
        self._user_loader = user_loader or _default_user_loader

    async def dispatch(self, request: Request, call_next):
        if os.getenv("WEB_AUTH_ENABLED", "1") == "0":
            return await call_next(request)

        path = request.url.path

        if _is_prefixed(path, PUBLIC_PREFIXES):
            return await call_next(request)

        # Prometheus скрейпит /metrics с localhost — пускаем без cookie.
        if path == "/metrics" and _is_local_client(request):
            return await call_next(request)

        user = await self._authenticate(request)
        if user is None:
            if _wants_html(request) and request.method == "GET":
                # next с query string: OIDC authorize (и любой GET с параметрами)
                # обязан вернуться на полный URL, не только path.
                next_url = request.url.path
                if request.url.query:
                    next_url += f"?{request.url.query}"
                return RedirectResponse(f"/login?next={quote(next_url)}", status_code=302)
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        request.state.user = user
        if user.role == "operator":
            return await call_next(request)
        if user.role == "radar" and _is_prefixed(path, RADAR_PREFIXES):
            return await call_next(request)

        # Аутентифицирован, но зона не его: radar-юзер в операторском setka.
        if _wants_html(request) and request.method == "GET":
            return RedirectResponse("/radar", status_code=302)
        return JSONResponse({"detail": "Forbidden for this role"}, status_code=403)

    async def _authenticate(self, request: Request):
        token = request.cookies.get(SESSION_COOKIE)
        if not token:
            return None
        payload = verify_session_token(token)
        if not payload:
            return None
        try:
            user = await self._user_loader(int(payload["uid"]))
        except Exception:  # noqa: BLE001 - БД недоступна → запрос не аутентифицирован
            logger.warning("AuthGate: user lookup failed", exc_info=True)
            return None
        if user is None or not user.is_active:
            return None
        # Смена пароля инвалидирует старые сессии: fragment в токене ≠ актуальному.
        # password_hash nullable с миграции 052 (соц-only аккаунты) — fragment
        # считаем от пустой строки, семантика инвалидации сохраняется.
        if payload.get("pf") != password_fragment(user.password_hash or ""):
            return None
        return user
