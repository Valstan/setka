"""AuthGateMiddleware — app-level auth + изоляция ролей (Ф0.1 контент-радара).

Secure by default: ВСЁ приложение закрыто, кроме явного allowlist'а
(PUBLIC_PREFIXES). Новый операторский route защищён автоматически — забыть
повесить зависимость невозможно, enforcement живёт в одном месте.

Роли (директива brain 2026-06-11, решение владельца раунд 2 §1):
- ``operator`` — весь setka (регионы/CRM/токены/мониторинг/...), **и только
  для аккаунтов владельца** (заказ владельца 2026-08-02, см. ниже).
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

Владелец (заказ 2026-08-02): сайт САРАФАНА видит **только его аккаунт**, и
только после входа через ЕСА. Роль ``operator`` сама по себе доступа больше
не даёт — решает личность (``_is_owner``: логин или ВК-id, оба входа
владельца обязаны работать). На домене сети ``сарафан.вмалмыже.рф``
посторонний — хоть аноним, хоть залогиненный radar-юзер — видит ровно одну
публичную ссылку ``/regions/links``; остальное, включая ``/services``,
уходит за вход.
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

# Открыто без auth по ТОЧНОМУ совпадению пути (с точностью до хвостового «/»).
# Публичный лендинг сети и его данные лежат здесь, а не в PUBLIC_PREFIXES:
# префиксное сравнение открывало бы заодно любой будущий путь, начинающийся с
# этих строк (`/regions/links-export` и т.п.). Пока лендинг был одной страницей
# среди прочих, цена ошибки была мала; с 2026-08-02 это единственная публичная
# дверь в домен, закрытый для всех, кроме владельца, — и она обязана быть узкой.
PUBLIC_EXACT = (
    "/regions/links",  # публичный лендинг «сообщества сети + реклама»
    "/api/regions/vk-links",  # его же данные (read-only список групп)
)

# Публичный домен сети (сарафан.вмалмыже.рф) и единственная публичная ссылка
# на нём. Заказ владельца 2026-08-02: сайт САРАФАНА виден только владельцу и
# только после входа через ЕСА; посторонний получает ровно витрину рекламы.
SARAFAN_CANONICAL_HOST_DEFAULT = "xn--80aaa6cmey.xn--80adkdyec4j.xn--p1ai"
LANDING_PATH = "/regions/links"

# Публично везде, КРОМЕ домена сети: на сарафане открыта ровно одна ссылка
# (LANDING_PATH), остальные страницы — под входом. Каталог сервисов при этом
# не пропадает из экосистемы: его публичная поверхность — вход.вмалмыже.рф
# (мандат brain 2026-08-01), туда же ведут кнопки «Сервисы» с других сайтов.
SARAFAN_CLOSED_PUBLIC = ("/services",)

# Обслуживание сессии и OIDC — не «содержимое сайта», поэтому правило домена
# сети их не глотает даже у постороннего. Иначе залогиненный чужой юзер не мог
# бы с этого хоста выйти из сессии (403 на logout — тупик), а front-channel
# OIDC-переход терял бы query и приезжал не туда. Дальше их всё равно судит
# обычная ролевая логика — доступа к содержимому это не даёт.
SARAFAN_ALLOWED_FOR_GUESTS = ("/api/auth/logout", "/api/auth/me", "/oidc/")

# Аккаунты владельца — единственные, кому открыт операторский SETKA (заказ
# владельца 2026-08-02: «внутренности сарафана видит только мой аккаунт»).
# Роль ``operator`` сама по себе больше не пропуск: саморегистрация выдаёт
# роль ``radar``, но роль в БД можно и поменять, а список аккаунтов — нет.
#
# Владелец входит ДВУМЯ путями, и оба обязаны работать, иначе он запрёт себя
# сам: логином/паролем (аккаунт ``valstan``) и через ВКонтакте на
# вход.вмалмыже.рф — там он приезжает своей ВК-личностью, у которой другой
# аккаунт и роль ``radar``. Поэтому владельца опознаём по личности, а не по
# роли: совпал логин ИЛИ ВК-id → пускаем в операторскую зону.
OWNER_LOGINS_DEFAULT = "valstan"
OWNER_VK_IDS_DEFAULT = "20002978"  # vk.ru/valstan_valstan, аккаунт «Валентин Савиных»

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


def _is_exact(path: str, paths: tuple) -> bool:
    """Точное совпадение пути, с точностью до хвостового «/» (см. PUBLIC_EXACT)."""
    normalized = path.rstrip("/") or "/"
    return any(normalized == p.rstrip("/") for p in paths)


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


def _radar_canonical_url(request: Request) -> Optional[str]:
    """Абсолютный адрес Радара на его домене — или ``None``, если он не настроен.

    Нужен, чтобы radar-юзера, забредшего на домен САРАФАНА, отправить домой,
    а не на витрину рекламы. Кука общая на ``.вмалмыже.рф`` — сессия переезд
    переживает.
    """
    try:
        from modules.radar_id.vk_upstream import radar_canonical_redirect

        return radar_canonical_redirect(request.url.hostname)
    except Exception:  # noqa: BLE001 - косметика маршрутизации не роняет гейт
        logger.warning("AuthGate: radar canonical resolve failed", exc_info=True)
        return None


def _on_sarafan_host(request: Request) -> bool:
    """Запрос пришёл на публичный домен сети (сарафан.вмалмыже.рф)?

    Сравнение — в punycode: кириллический хост и его ASCII-форма это одно имя.
    Переопределяется env ``SARAFAN_CANONICAL_HOST`` (пустое значение выключает
    поведение — например, если домен когда-нибудь сменится).
    """
    canonical = _idna(os.getenv("SARAFAN_CANONICAL_HOST", SARAFAN_CANONICAL_HOST_DEFAULT))
    host = _idna(request.url.hostname or "")
    return bool(canonical and host and host == canonical)


def _csv_env(name: str, default: str) -> frozenset:
    """Множество значений из env-списка через запятую, в нижнем регистре.

    Пустая или пробельная переменная = «не задано» → берём дефолт. Это не
    придирка: пустой ``SETKA_OWNER_LOGINS=`` в `/etc/setka/setka.env` иначе
    означал бы «владельцев нет вообще» и запер бы владельца снаружи своего же
    сайта. Отключать правило пустым значением нельзя — только сменить состав.
    """
    raw = (os.getenv(name) or "").strip() or default
    return frozenset(part.strip().lower() for part in raw.split(",") if part.strip())


def _is_owner(user) -> bool:
    """Аккаунт владельца? Опознаём по логину ИЛИ по ВК-id (см. константы выше).

    Оба идентификатора необязательны у конкретного аккаунта: у ВК-входа логин
    пустой, у парольного аккаунта нет ``vk_user_id``. Совпадения по пустому
    значению быть не должно — отсюда явные проверки на непустоту.

    ⚠️ Логин-ветка ДОПОЛНИТЕЛЬНО требует роль ``operator``, и это не
    перестраховка. Логин выбирает тот, кто регистрируется: ``POST
    /api/auth/register`` публичен, инвайт-код по решению владельца роздан
    соседним проектам, а проверка занятости логина в Postgres регистро-
    зависима — значит «VALSTAN» заводится рядом с «valstan» и без этой
    проверки роли прошёл бы сюда как владелец. Роль же публичная регистрация
    жёстко ставит ``radar`` (web/api/auth.py), поднять её можно только с бокса
    (``scripts/create_radar_user.py --role operator``). ВК-ветке такая защита
    не нужна: ``vk_user_id`` приезжает от ВКонтакте, пользователь его не
    выбирает — поэтому владелец, вошедший через ВК с ролью ``radar``, проходит.
    """
    login = (getattr(user, "login", None) or "").strip().lower()
    if (
        login
        and getattr(user, "role", "") == "operator"
        and login in _csv_env("SETKA_OWNER_LOGINS", OWNER_LOGINS_DEFAULT)
    ):
        return True
    vk_id = getattr(user, "vk_user_id", None)
    if vk_id is not None and str(vk_id).strip():
        return str(vk_id).strip().lower() in _csv_env("SETKA_OWNER_VK_IDS", OWNER_VK_IDS_DEFAULT)
    return False


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

        on_sarafan = _on_sarafan_host(request)
        if _is_exact(path, PUBLIC_EXACT):
            return await call_next(request)
        if _is_prefixed(path, PUBLIC_PREFIXES) and not (
            on_sarafan and _is_prefixed(path, SARAFAN_CLOSED_PUBLIC)
        ):
            return await call_next(request)

        # Prometheus скрейпит /metrics с localhost — пускаем без cookie.
        if path == "/metrics" and _is_local_client(request):
            return await call_next(request)

        user = await self._authenticate(request)
        if user is None:
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

        # Владелец — единственный, кому открыт сайт САРАФАНА; его личность
        # старше роли (ВК-вход даёт ему аккаунт с ролью radar, см. _is_owner).
        if _is_owner(user):
            return await call_next(request)

        # Дальше — все, кто НЕ владелец. На домене сети им не видно ничего,
        # кроме витрины: заказ владельца 2026-08-02. Роль radar своей зоной
        # пользуется на своём домене (радар.вмалмыже.рф), туда и отправляем.
        if on_sarafan and not _is_prefixed(path, SARAFAN_ALLOWED_FOR_GUESTS):
            if request.method == "GET" and _wants_html(request):
                target = None
                if user.role == "radar":
                    target = _radar_canonical_url(request)
                return RedirectResponse(target or LANDING_PATH, status_code=302)
            return JSONResponse({"detail": "Forbidden on this host"}, status_code=403)

        if user.role == "radar" and (
            _is_prefixed(path, RADAR_PREFIXES) or (path == "/" and _on_radar_host(request))
        ):
            # На каноническом радар-хосте интерфейс Радара живёт на корне —
            # для radar-роли «/» там своя зона, а не операторский дашборд.
            return await call_next(request)

        # Аутентифицирован, но зона не его: не-владелец в операторском setka
        # (роль radar — обычный случай; роль operator без владельческого
        # аккаунта — тоже сюда, роль сама по себе доступа больше не даёт).
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
