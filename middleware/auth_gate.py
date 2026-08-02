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

Исключение — front-channel GET-эндпоинты (OIDC authorize, FRONT_CHANNEL_GET_PATHS):
в них по спеку всегда приходят через redirect браузера, поэтому неаутентифи-
цированный GET **всегда** → 302 на /login, даже без Accept: text/html. Иначе
curl/мониторинг без браузерного заголовка видит ложный 401 «сломан» (запрос
trener через brain 2026-07-10).

Kill-switch: env ``WEB_AUTH_ENABLED=0`` отключает гейт целиком (локальный dev
без БД-юзеров). Дефолт — включено; на проде не выключать.

/metrics — особый случай: Prometheus скрейпит с localhost; снаружи метрики
не отдаём (оператору залогиненным — можно).

Корень публичного домена сети (``сарафан.вмалмыже.рф``) для неаутентифи-
цированного GET — не вход, а витрина: 302 на ``/regions/links`` (заказ
владельца 2026-08-02, короткий адрес для покупателей рекламы). Оператор с
сессией на том же хосте получает обычный дашборд.
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
    "/services",  # каталог сервисов экосистемы — публичная витрина ссылок
    "/regions/links",  # публичный лендинг «сообщества сети + реклама» (заказ
    # владельца 2026-07-29). ТОЛЬКО этот точный путь: префикс-матчер сравнивает
    # p.rstrip("/") или startswith("/regions/links"), сам /regions и
    # /regions/{code} остаются операторскими.
    "/api/regions/vk-links",  # данные того же лендинга (read-only список групп)
    "/static/",
    "/favicon.ico",
    "/api/health",  # internal watchdogs/CI ходят на 127.0.0.1:8000 без cookie
    "/api/gateway/",  # VK-шлюз: своя X-API-Key защита (web/api/gateway.py).
    # trailing slash важен: /api/gateway-stats НЕ public — это операторская
    # статистика под сессионной cookie (web/api/gateway_stats.py).
    "/api/classifier/",  # HITL-классификатор ingest (облачная рутина): своя
    # X-API-Key защита (web/api/classifier_ingest.py). Аналогично шлюзу,
    # trailing slash важен: /api/classifier-review НЕ public (операторская лента).
    "/api/ecosystem/",  # self-serve подключение проектов (ADR-0010): своя
    # X-Ecosystem-Key защита (web/api/ecosystem.py). Заявку шлёт сервер
    # проекта-клиента, сессионной cookie у него нет и быть не может.
    "/api/auth/login",
    "/api/auth/register",
    "/.well-known/",
    # Радар-ID OIDC (web/api/radar_id.py): token/userinfo зовут серверы
    # клиентов без cookie — своя client-auth (secret/PKCE/Bearer).
    # /oidc/authorize сюда НЕ входит — ему нужна сессия пользователя.
    "/oidc/token",
    "/oidc/userinfo",
    # ВК-вход Радар-ID: пользователь ещё НЕ аутентифицирован (это и есть вход).
    "/auth/vk/",
)

# Публичный домен сети и его витрина (заказ владельца 2026-08-02). Корень
# сарафан.вмалмыже.рф — адрес «для покупателей рекламы»: он обязан открывать
# лендинг, а не уводить неизвестного посетителя на страницу входа экосистемы.
SARAFAN_CANONICAL_HOST_DEFAULT = "xn--80aaa6cmey.xn--80adkdyec4j.xn--p1ai"
LANDING_PATH = "/regions/links"

# Front-channel GET-эндпоинты: в них ходят только через redirect браузера
# (OIDC authorization endpoint). Неаутентифицированный GET сюда → ВСЕГДА 302 на
# /login, независимо от Accept — чтобы curl/мониторинг без браузерного заголовка
# не получал ложный 401 (запрос trener через brain 2026-07-10). Точное сравнение
# пути: не хотим ловить гипотетический /oidc/authorize-что-то.
FRONT_CHANNEL_GET_PATHS = ("/oidc/authorize",)

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


def _idna(host: str) -> str:
    """Хост в ASCII (кириллический и punycode — одно и то же имя)."""
    host = (host or "").strip().lower().rstrip(".")
    try:
        return host.encode("idna").decode("ascii")
    except (UnicodeError, UnicodeDecodeError):
        return host


def _on_radar_host(request: Request) -> bool:
    """Запрос пришёл на канонический хост Радара (радар.вмалмыже.рф)?"""
    try:
        from modules.radar_id.vk_upstream import is_radar_host

        return is_radar_host(request.url.hostname)
    except Exception:  # noqa: BLE001 - хост-детект не должен ронять гейт
        return False


def _on_sarafan_host(request: Request) -> bool:
    """Запрос пришёл на публичный домен сети (сарафан.вмалмыже.рф)?

    Сравнение — в punycode: кириллический хост и его ASCII-форма это одно имя.
    Переопределяется env ``SARAFAN_CANONICAL_HOST`` (пустое значение выключает
    поведение — например, если домен когда-нибудь сменится).
    """
    canonical = _idna(os.getenv("SARAFAN_CANONICAL_HOST", SARAFAN_CANONICAL_HOST_DEFAULT))
    host = _idna(request.url.hostname or "")
    return bool(canonical and host and host == canonical)


def _login_redirect(request: Request, next_url: str) -> str:
    """URL страницы входа для неаутентифицированного браузерного GET.

    Единый вход экосистемы (заказ владельца 2026-07-26): пользователь на
    сервисном поддомене ``*.вмалмыже.рф`` (радар, сарафан, ...) отправляется
    не на локальный ``/login`` того же хоста, а на **центральную** страницу
    входа issuer'а (``вход.вмалмыже.рф``) с абсолютным ``next`` — выбрал там
    способ входа, вернулся на родной домен авторизованным (кука выдаётся на
    весь ``.вмалмыже.рф``).

    Граница доверия та же, что у возврата после ВК-входа: хост обязан делить
    с нами сессионную куку (``SESSION_COOKIE_DOMAIN``). Хост вне зоны, сам
    issuer или локальная разработка (зона не задана) → прежний относительный
    ``/login`` — деградация, не смена поведения.
    """
    local = f"/login?next={quote(next_url)}"
    try:
        from urllib.parse import urlsplit

        from config.radar_id import get_issuer
        from modules.radar_id.vk_upstream import host_shares_session

        host = _idna(request.url.hostname or "")
        issuer = get_issuer()
        issuer_host = _idna(urlsplit(issuer).hostname or "")
        if not host or not issuer_host or host == issuer_host:
            return local
        if not host_shares_session(host):
            return local
        absolute_next = f"https://{host}{next_url}"
        return f"{issuer}/login?next={quote(absolute_next)}"
    except Exception:  # noqa: BLE001 - логин важнее косметики единого входа
        logger.warning("AuthGate: central login resolve failed", exc_info=True)
        return local


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
            # Корень публичного домена сети для постороннего = витрина, не вход.
            # Покупателю рекламы дают короткий адрес «сарафан.вмалмыже.рф», и
            # страница входа экосистемы на нём выглядит как «сюда нельзя».
            # Оператор с сессией на том же хосте по-прежнему видит дашборд.
            if request.method == "GET" and path == "/" and _on_sarafan_host(request):
                return RedirectResponse(LANDING_PATH, status_code=302)
            # Редирект на login для браузерного GET, а также для front-channel
            # GET-путей (OIDC authorize) даже без браузерного Accept — они
            # достижимы только через redirect user-agent'а, 401 там бессмыслен.
            redirect_to_login = request.method == "GET" and (
                _wants_html(request) or path in FRONT_CHANNEL_GET_PATHS
            )
            if redirect_to_login:
                # next с query string: OIDC authorize (и любой GET с параметрами)
                # обязан вернуться на полный URL, не только path.
                next_url = request.url.path
                if request.url.query:
                    next_url += f"?{request.url.query}"
                return RedirectResponse(_login_redirect(request, next_url), status_code=302)
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)

        request.state.user = user
        if user.role == "operator":
            return await call_next(request)
        if user.role == "radar" and (
            _is_prefixed(path, RADAR_PREFIXES) or (path == "/" and _on_radar_host(request))
        ):
            # На каноническом радар-хосте интерфейс Радара живёт на корне —
            # для radar-роли «/» там своя зона, а не операторский дашборд.
            return await call_next(request)

        # Аутентифицирован, но зона не его: radar-юзер в операторском setka.
        if _wants_html(request) and request.method == "GET":
            return RedirectResponse("/" if _on_radar_host(request) else "/radar", status_code=302)
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
