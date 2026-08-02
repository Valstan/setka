"""Tests for middleware/auth_gate.py — изоляция ролей operator|radar (Ф0.1).

Мини-FastAPI-приложение + TestClient; user_loader инжектится (БД не нужна).
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from middleware.auth_gate import AuthGateMiddleware
from modules.radar import auth

OPERATOR_HASH = auth.hash_password("op-password")
RADAR_HASH = auth.hash_password("radar-password")

OPERATOR = SimpleNamespace(
    id=1, role="operator", is_active=True, password_hash=OPERATOR_HASH, login="op"
)
RADAR = SimpleNamespace(id=2, role="radar", is_active=True, password_hash=RADAR_HASH, login="ra")
INACTIVE = SimpleNamespace(
    id=3, role="operator", is_active=False, password_hash=OPERATOR_HASH, login="off"
)
# Роль operator, но логин не в allowlist владельца — доступа больше не даёт.
INTRUDER = SimpleNamespace(
    id=4, role="operator", is_active=True, password_hash=OPERATOR_HASH, login="intruder"
)
# ВК-вход владельца: логина нет вовсе, роль radar, личность — по vk_user_id.
OWNER_VK = SimpleNamespace(
    id=5, role="radar", is_active=True, password_hash=None, login=None, vk_user_id=20002978
)

USERS = {1: OPERATOR, 2: RADAR, 3: INACTIVE, 4: INTRUDER, 5: OWNER_VK}


async def _loader(uid):
    return USERS.get(uid)


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SETKA_WEB_SECRET", "gate-test-secret")
    monkeypatch.delenv("WEB_AUTH_ENABLED", raising=False)
    # Владелец в тестах — фикстурный OPERATOR (login="op"); прод-дефолт
    # ("valstan") здесь не подходит, а роль сама по себе доступа не даёт.
    monkeypatch.setenv("SETKA_OWNER_LOGINS", "op")
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "20002978")


@pytest.fixture
def client():
    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.get("/login")
    async def login_page():
        return {"page": "login"}

    @app.get("/api/health/full")
    async def health():
        return {"ok": True}

    @app.get("/api/regions")
    async def regions():
        return {"operator": "zone"}

    @app.get("/regions/links")
    async def region_links_landing():
        return {"page": "landing"}

    @app.get("/api/regions/vk-links")
    async def region_links_data():
        return {"blocks": []}

    @app.get("/")
    async def dashboard():
        return {"page": "dashboard"}

    @app.get("/radar")
    async def radar_page():
        return {"page": "radar"}

    @app.get("/oidc/authorize")
    async def oidc_authorize():
        return {"page": "authorize"}

    @app.get("/api/radar/sources")
    async def radar_api():
        return {"radar": "zone"}

    return TestClient(app)


def _cookie_for(user) -> dict:
    # password_hash nullable у соц-аккаунтов (миграция 052) — гейт считает
    # fragment от "", помощник обязан повторять это, а не падать.
    token = auth.issue_session_token(
        user.id, user.role, auth.password_fragment(user.password_hash or "")
    )
    return {auth.SESSION_COOKIE: token}


# ─── Публичные пути ──────────────────────────────────────────────


def test_public_paths_open_without_cookie(client):
    assert client.get("/login").status_code == 200
    assert client.get("/api/health/full").status_code == 200


def test_public_landing_open_but_operator_regions_stay_closed(client):
    """/regions/links + его API публичны (лендинг 2026-07-29); сам /regions
    и остальной операторский API остаются под замком."""
    assert client.get("/regions/links").status_code == 200
    assert client.get("/api/regions/vk-links").status_code == 200
    assert client.get("/api/regions").status_code == 401


# ─── Неаутентифицированные ───────────────────────────────────────


def test_api_without_cookie_is_401(client):
    resp = client.get("/api/regions")
    assert resp.status_code == 401


def test_browser_get_redirects_to_login(client):
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_garbage_cookie_is_401(client):
    client.cookies.set(auth.SESSION_COOKIE, "garbage")
    assert client.get("/api/regions").status_code == 401


def test_oidc_authorize_redirects_to_login_without_browser_accept(client):
    # front-channel GET: curl/мониторинг без Accept: text/html должен получить
    # 302 на login, а не ложный 401 (запрос trener через brain 2026-07-10).
    resp = client.get(
        "/oidc/authorize?client_id=trener&response_type=code",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith("/login?next=")
    # query authorize-запроса сохраняется в next, чтобы после логина вернуться.
    assert "client_id" in loc and "response_type" in loc


def test_oidc_authorize_still_401_for_non_get(client):
    # POST на front-channel путь не редиректим — только GET (спек authorize=GET).
    resp = client.post("/oidc/authorize", follow_redirects=False)
    assert resp.status_code == 401


# ─── Роли ────────────────────────────────────────────────────────


def test_operator_reaches_operator_zone(client):
    client.cookies.update(_cookie_for(OPERATOR))
    assert client.get("/api/regions").status_code == 200
    assert client.get("/radar").status_code == 200  # оператору радар тоже открыт


def test_radar_reaches_radar_zone_only(client):
    client.cookies.update(_cookie_for(RADAR))
    assert client.get("/radar").status_code == 200
    assert client.get("/api/radar/sources").status_code == 200
    assert client.get("/api/regions").status_code == 403


def test_radar_browser_redirected_to_radar_from_operator_pages(client):
    client.cookies.update(_cookie_for(RADAR))
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == "/radar"


# ─── Инвалидация сессий ──────────────────────────────────────────


def test_inactive_user_is_401(client):
    client.cookies.update(_cookie_for(INACTIVE))
    assert client.get("/api/regions").status_code == 401


def test_password_change_invalidates_session(client):
    token = auth.issue_session_token(1, "operator", "stale-fragment-")
    client.cookies.set(auth.SESSION_COOKIE, token)
    assert client.get("/api/regions").status_code == 401


# ─── Kill-switch ─────────────────────────────────────────────────


def test_kill_switch_disables_gate(client, monkeypatch):
    monkeypatch.setenv("WEB_AUTH_ENABLED", "0")
    assert client.get("/api/regions").status_code == 200


# ─── Единый вход: редирект на центральный /login (заказ владельца 2026-07-26) ─

_COOKIE_DOMAIN = ".xn--80adkdyec4j.xn--p1ai"  # .вмалмыже.рф
_ISSUER_HOST = "xn--b1ae3a1a.xn--80adkdyec4j.xn--p1ai"  # вход.вмалмыже.рф
_RADAR_HOST = "xn--80aal0cd.xn--80adkdyec4j.xn--p1ai"  # радар.вмалмыже.рф


def _client_for_host(host: str):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.get("/radar")
    async def radar_page():
        return {"page": "radar"}

    return TestClient(app, base_url=f"https://{host}")


def test_service_subdomain_redirects_to_central_login(monkeypatch):
    """Неавторизованный на радар.вмалмыже.рф → вход.вмалмыже.рф с абсолютным next."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    resp = _client_for_host(_RADAR_HOST).get(
        "/radar", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 302
    loc = resp.headers["location"]
    assert loc.startswith(f"https://{_ISSUER_HOST}/login?next=")
    from urllib.parse import parse_qs, urlsplit

    next_val = parse_qs(urlsplit(loc).query)["next"][0]
    assert next_val == f"https://{_RADAR_HOST}/radar"


def test_issuer_host_keeps_local_login(monkeypatch):
    """На самом вход.вмалмыже.рф — прежний относительный /login."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    resp = _client_for_host(_ISSUER_HOST).get(
        "/radar", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_foreign_host_keeps_local_login(monkeypatch):
    """Хост вне куки-зоны (техдомен, локалка) — без межхостового редиректа."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    resp = _client_for_host("testserver.example").get(
        "/radar", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


def test_no_cookie_domain_keeps_local_login(monkeypatch):
    """Локальная разработка (зона не задана) — поведение прежнее."""
    monkeypatch.delenv("SESSION_COOKIE_DOMAIN", raising=False)
    resp = _client_for_host(_RADAR_HOST).get(
        "/radar", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith("/login?next=")


# ─── Домен сети сарафан.вмалмыже.рф: только владелец ─────────────
#
# Заказ владельца 2026-08-02: сайт САРАФАНА виден только его аккаунту и только
# после входа через ЕСА; посторонний (аноним или чужой залогиненный юзер) видит
# ровно одну публичную ссылку — /regions/links.

_SARAFAN_HOST = "xn--80aaa6cmey.xn--80adkdyec4j.xn--p1ai"  # сарафан.вмалмыже.рф
_TECH_HOST = "3931b3fe50ab.vps.myjino.ru"  # технический адрес того же приложения


def _sarafan_client(host: str):
    """Мини-приложение с дашбордом, витриной, каталогом и операторским API."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.get("/")
    async def dashboard():
        return {"page": "dashboard"}

    @app.get("/regions/links")
    async def landing():
        return {"page": "landing"}

    @app.get("/services")
    async def services():
        return {"page": "services"}

    @app.get("/api/regions")
    async def regions():
        return {"operator": "zone"}

    @app.get("/radar")
    async def radar_page():
        return {"page": "radar"}

    return TestClient(app, base_url=f"https://{host}")


def test_sarafan_root_sends_anonymous_to_central_login(monkeypatch):
    """Корень домена сети — сайт под входом, а не витрина (откат 2026-08-02)."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    resp = _sarafan_client(_SARAFAN_HOST).get(
        "/", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert resp.status_code == 302
    assert resp.headers["location"].startswith(f"https://{_ISSUER_HOST}/login?next=")


def test_sarafan_landing_stays_public_for_anonymous(monkeypatch):
    """Единственная публичная ссылка домена — витрина рекламы."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    assert _sarafan_client(_SARAFAN_HOST).get("/regions/links").status_code == 200


def test_sarafan_closes_services_but_other_hosts_keep_it(monkeypatch):
    """Каталог сервисов публичен на ЕСА и техдомене, но не на домене сети."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    closed = _sarafan_client(_SARAFAN_HOST).get(
        "/services", headers={"Accept": "text/html"}, follow_redirects=False
    )
    assert closed.status_code == 302
    assert _sarafan_client(_ISSUER_HOST).get("/services").status_code == 200
    assert _sarafan_client(_TECH_HOST).get("/services").status_code == 200


def test_sarafan_owner_sees_dashboard(monkeypatch):
    """Владелец (логин в allowlist) получает обычный дашборд."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(OPERATOR))
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.json() == {"page": "dashboard"}


def test_sarafan_owner_via_vk_identity_sees_dashboard(monkeypatch):
    """ВК-вход владельца: логина нет, роль radar — пускаем по vk_user_id.

    Это не украшение: вход через ВКонтакте на вход.вмалмыже.рф — штатный путь
    владельца, и опознание строго по роли заперло бы его снаружи своего сайта.
    """
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(OWNER_VK))
    resp = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 200
    assert resp.json() == {"page": "dashboard"}


def test_sarafan_non_owner_operator_gets_landing_and_403(monkeypatch):
    """Роль operator без владельческого аккаунта доступа не даёт нигде."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(INTRUDER))
    page = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
    assert page.status_code == 302
    assert page.headers["location"] == "/regions/links"
    assert client.get("/api/regions").status_code == 403

    tech = _sarafan_client(_TECH_HOST)
    tech.cookies.update(_cookie_for(INTRUDER))
    assert tech.get("/api/regions").status_code == 403


def test_sarafan_radar_user_is_sent_to_radar_host(monkeypatch):
    """Чужой radar-юзер на домене сети уезжает домой, а не на витрину."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(RADAR))
    resp = client.get("/radar", headers={"Accept": "text/html"}, follow_redirects=False)
    assert resp.status_code == 302
    assert resp.headers["location"] == f"https://{_RADAR_HOST}/"
    assert client.get("/api/radar/sources").status_code == 403


def test_radar_user_keeps_own_host(monkeypatch):
    """Правило домена сети не тронуло зону Радара на его собственном хосте."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_RADAR_HOST)
    client.cookies.update(_cookie_for(RADAR))
    assert client.get("/radar").status_code == 200


def test_owner_login_match_is_case_insensitive(monkeypatch):
    """«Op» и «op» — один и тот же аккаунт."""
    monkeypatch.setenv("SETKA_OWNER_LOGINS", "OP")
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(OPERATOR))
    assert client.get("/api/regions").status_code == 200


def test_empty_owner_env_falls_back_to_default(monkeypatch):
    """Пустая переменная = «не задано», а не «владельцев нет» — иначе lockout."""
    from middleware import auth_gate

    monkeypatch.setenv("SETKA_OWNER_LOGINS", "   ")
    assert auth_gate._csv_env("SETKA_OWNER_LOGINS", "valstan") == frozenset({"valstan"})


def test_empty_login_never_matches_owner(monkeypatch):
    """Аккаунт без логина не должен совпасть с пустым элементом списка."""
    from middleware import auth_gate

    monkeypatch.setenv("SETKA_OWNER_LOGINS", "valstan,,")
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "1")
    nobody = SimpleNamespace(id=99, role="radar", is_active=True, login="", vk_user_id=None)
    assert auth_gate._is_owner(nobody) is False


# ─── Что нашли критики плана (адверсариальный прогон 2026-08-02) ──


def test_owner_login_alone_is_not_enough_without_operator_role(monkeypatch):
    """Саморегистрация чужого регистра НЕ делает владельцем.

    Публичный POST /api/auth/register требует инвайт-код, который роздан
    соседним проектам, а уникальность login в Postgres регистрозависима —
    значит «VALSTAN» мог бы завестись рядом с «valstan». Логин-ветку
    `_is_owner` поэтому сторожит роль: регистрация выдаёт radar, поднять до
    operator можно только с бокса.
    """
    from middleware import auth_gate

    monkeypatch.setenv("SETKA_OWNER_LOGINS", "valstan")
    impostor = SimpleNamespace(id=90, role="radar", is_active=True, login="VALSTAN")
    assert auth_gate._is_owner(impostor) is False
    real = SimpleNamespace(id=91, role="operator", is_active=True, login="VALSTAN")
    assert auth_gate._is_owner(real) is True


def test_impostor_with_owner_login_sees_nothing_on_sarafan(monkeypatch):
    """Тот же сценарий сквозь гейт: витрина вместо дашборда, 403 на API."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    monkeypatch.setenv("SETKA_OWNER_LOGINS", "op")
    impostor = SimpleNamespace(
        id=92, role="radar", is_active=True, password_hash=RADAR_HASH, login="OP"
    )
    USERS[92] = impostor
    try:
        client = _sarafan_client(_SARAFAN_HOST)
        client.cookies.update(_cookie_for(impostor))
        page = client.get("/", headers={"Accept": "text/html"}, follow_redirects=False)
        assert page.status_code == 302 and page.headers["location"] != "/"
        assert client.get("/api/regions").status_code == 403
    finally:
        USERS.pop(92, None)


def test_owner_vk_branch_rejects_foreign_and_missing_ids(monkeypatch):
    """ВК-ветка: чужой id, пустой id и None — не владелец."""
    from middleware import auth_gate

    monkeypatch.setenv("SETKA_OWNER_LOGINS", "valstan")
    monkeypatch.setenv("SETKA_OWNER_VK_IDS", "20002978,,")
    assert auth_gate._is_owner(SimpleNamespace(role="radar", login=None, vk_user_id=1)) is False
    assert auth_gate._is_owner(SimpleNamespace(role="radar", login=None, vk_user_id=None)) is False
    assert auth_gate._is_owner(SimpleNamespace(role="radar", login=None, vk_user_id="")) is False
    # id из БД приезжает целым числом, а из env — строкой: сравнение обязано
    # работать в обе стороны, иначе владелец молча перестаёт быть владельцем.
    assert auth_gate._is_owner(SimpleNamespace(role="radar", login=None, vk_user_id=20002978))
    assert auth_gate._is_owner(SimpleNamespace(role="radar", login=None, vk_user_id="20002978"))


def test_is_owner_survives_incomplete_user_model():
    """Гейт не имеет права падать 500 на аккаунте без полей login/vk_user_id."""
    from middleware import auth_gate

    assert auth_gate._is_owner(SimpleNamespace()) is False


def test_owner_allowlist_accepts_several_accounts(monkeypatch):
    from middleware import auth_gate

    monkeypatch.setenv("SETKA_OWNER_LOGINS", " valstan , Second_Owner ")
    for login in ("valstan", "second_owner", "SECOND_OWNER"):
        user = SimpleNamespace(role="operator", is_active=True, login=login)
        assert auth_gate._is_owner(user) is True
    stranger = SimpleNamespace(role="operator", is_active=True, login="third")
    assert auth_gate._is_owner(stranger) is False


def test_landing_public_path_is_exact_not_prefix(monkeypatch):
    """`/regions/links` публичен точным путём: соседний путь с тем же началом — нет.

    Префиксное сравнение открыло бы любой будущий `/regions/links-*` анониму,
    а это единственная дверь в домен, закрытый для всех, кроме владельца.
    """
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.get("/regions/links")
    async def landing():
        return {"page": "landing"}

    @app.get("/regions/links-export")
    async def sneaky():
        return {"page": "export"}

    client = TestClient(app, base_url=f"https://{_SARAFAN_HOST}")
    assert client.get("/regions/links").status_code == 200
    assert client.get("/regions/links/").status_code == 200  # хвостовой слэш — тот же путь
    assert client.get("/regions/links-export").status_code == 401


def test_machine_doors_stay_open_on_sarafan_host(monkeypatch):
    """Серверные двери с собственной аутентификацией правило домена не глотает.

    Если затянуть их в owner-only периметр, watchdog'и, VK-шлюз, ingest
    облачной рутины и OIDC-клиенты соседей умрут молча.
    """
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    machine_paths = (
        "/api/health/full",
        "/api/gateway/call",
        "/api/classifier/pending",
        "/api/ecosystem/oidc-clients",
        "/.well-known/openid-configuration",
        "/oidc/token",
        "/auth/vk/login",
    )
    for machine_path in machine_paths:

        @app.get(machine_path)
        async def machine_door():
            return {"door": "open"}

    client = TestClient(app, base_url=f"https://{_SARAFAN_HOST}")
    for machine_path in machine_paths:
        assert client.get(machine_path).status_code == 200, machine_path


def test_guest_can_log_out_from_sarafan_host(monkeypatch):
    """Чужой залогиненный юзер обязан суметь выйти с домена сети (не 403-тупик)."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.add_middleware(AuthGateMiddleware, user_loader=_loader)

    @app.post("/api/auth/logout")
    async def logout():
        return {"ok": True}

    client = TestClient(app, base_url=f"https://{_SARAFAN_HOST}")
    client.cookies.update(_cookie_for(RADAR))
    assert client.post("/api/auth/logout").status_code == 200


def test_owner_still_sees_services_on_sarafan(monkeypatch):
    """Закрытие публичности каталога не закрывает его для самого владельца."""
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(OPERATOR))
    assert client.get("/services").status_code == 200


def test_owner_allowlist_does_not_bypass_authentication(monkeypatch):
    """Деактивированный аккаунт владельца — 401, а не дашборд.

    Порядок проверок (сначала `_authenticate`, потом `_is_owner`) сторожим
    тестом: перестановка «ради удобства владельца» открыла бы сайт по старой
    куке отключённого аккаунта.
    """
    monkeypatch.setenv("SESSION_COOKIE_DOMAIN", _COOKIE_DOMAIN)
    monkeypatch.setenv("SETKA_OWNER_LOGINS", "off")  # INACTIVE.login, is_active=False
    client = _sarafan_client(_SARAFAN_HOST)
    client.cookies.update(_cookie_for(INACTIVE))
    assert client.get("/api/regions").status_code == 401


def test_kill_switch_opens_everything_documented(monkeypatch):
    """WEB_AUTH_ENABLED=0 обнуляет и правило домена сети. На проде не выключать.

    Тест не одобряет дыру, а делает её видимой: kill-switch задуман для
    локальной разработки без БД-юзеров, и цена его включения на проде теперь
    выше, чем была.
    """
    monkeypatch.setenv("WEB_AUTH_ENABLED", "0")
    client = _sarafan_client(_SARAFAN_HOST)
    assert client.get("/").status_code == 200
