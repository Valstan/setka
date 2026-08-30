"""API страницы «Темы и доли» (заказ владельца 2026-08-30).

Страница управляет тем, что выходит на стены районов, поэтому тесты стерегут
именно те свойства, которые ломаются молча:

* ``null`` снимает потолок, а отсутствие ключа не трогает тему — две разные
  вещи, и перепутать их значит либо потерять настройку, либо снять чужую;
* доля вне 0..100 отбивается схемой, а не записывается в базу;
* неизвестная тема даёт 404, а не тихо создаёт призрака, чья доля не применится
  никогда;
* служебным темам доля не назначается: «мусор» не публикуется вовсе, «соседи»
  идут отдельным каналом, и ползунок для них был бы ручкой без провода;
* страница показывает РЕАЛЬНОЕ состояние гейта потолков.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import database.models  # noqa: F401 — конфигурация мапперов
from database.connection import Base
from database.models_extended import (
    ClassifierTheme,
    ClassifierThemeAlias,
    ContentClassification,
    PublishedPost,
)
from web.api import theme_quotas as tq


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(tq.router, prefix="/api/theme-quotas")
    return TestClient(app)


# ───────── схема входа ─────────


def test_null_share_is_allowed_it_means_no_cap():
    body = tq.SharesPut(shares={"новости": None})
    assert body.shares == {"новости": None}


def test_missing_key_is_not_the_same_as_null():
    # Ключа нет — тему не трогаем; null — снимаем потолок. Схема обязана
    # различать эти случаи, иначе сохранение одной темы снимет настройку соседней.
    body = tq.SharesPut(shares={"новости": 50})
    assert "спорт" not in body.shares


@pytest.mark.parametrize("bad", [-1, 101, 1000])
def test_share_outside_range_is_rejected(bad):
    with pytest.raises(ValidationError):
        tq.SharesPut(shares={"новости": bad})


@pytest.mark.parametrize("good", [0, 0.5, 100])
def test_share_inside_range_is_accepted(good):
    assert tq.SharesPut(shares={"новости": good}).shares["новости"] == good


# ───────── арифметика колонок ─────────


def test_pct_of_empty_denominator_is_none_not_zero():
    # Первые сутки после релиза журнал пуст. «0%» сказало бы «темы не было»,
    # а правда — «мерить ещё не по чему».
    assert tq._pct(0, 0) is None
    assert tq._pct(5, 0) is None


def test_pct_rounds_to_one_decimal():
    assert tq._pct(1, 3) == 33.3


# ───────── маршруты ─────────


def _routes(method):
    out = []
    for route in tq.router.routes:
        if method in getattr(route, "methods", set()):
            out.append(route.path)
    return out


def test_normalize_is_not_shadowed_by_the_root_route():
    """Регресс на прод-инцидент 2026-08-19: статический путь, объявленный после
    накрывающего его параметризованного, становится недостижим, а фронт молча
    гасит ошибку. Здесь параметризованных путей нет вовсе — тест стережёт, чтобы
    они не появились без пересмотра порядка."""
    posts = _routes("POST")
    assert "/normalize" in posts
    assert not [
        p for p in posts if "{" in p
    ], "появился параметризованный POST — проверить, не накрывает ли он /normalize"


def test_root_answers_with_and_without_trailing_slash():
    # Фронт ходит на /api/theme-quotas/, а curl из памятки — без слеша. Редирект
    # 307 на PUT теряет тело в части клиентов, поэтому объявлены оба пути.
    gets = _routes("GET")
    assert "" in gets and "/" in gets


# ───────── PUT ─────────


def test_unknown_theme_gives_404_not_a_ghost(client, monkeypatch):
    async def fake_execute(*_a, **_k):
        class R:
            def scalars(self_inner):
                class S:
                    def all(self_s):
                        return []

                return S()

        return R()

    db = AsyncMock()
    db.execute = fake_execute
    client.app.dependency_overrides[tq.get_db_session] = lambda: db

    r = client.put("/api/theme-quotas/", json={"shares": {"нетакой": 10}})
    assert r.status_code == 404
    assert "нетакой" in r.json()["detail"]


def test_service_theme_cannot_get_a_share(client):
    class Row:
        def __init__(self, name, is_service):
            self.name = name
            self.is_service = is_service
            self.share_percent = None

    rows = [Row("мусор", True)]

    async def fake_execute(*_a, **_k):
        class R:
            def scalars(self_inner):
                class S:
                    def all(self_s):
                        return rows

                return S()

        return R()

    db = AsyncMock()
    db.execute = fake_execute
    client.app.dependency_overrides[tq.get_db_session] = lambda: db

    r = client.put("/api/theme-quotas/", json={"shares": {"мусор": 10}})
    assert r.status_code == 400
    assert "служебным" in r.json()["detail"]
    assert rows[0].share_percent is None


def test_empty_payload_is_a_noop(client):
    db = AsyncMock()
    client.app.dependency_overrides[tq.get_db_session] = lambda: db
    r = client.put("/api/theme-quotas/", json={"shares": {}})
    assert r.status_code == 200
    assert r.json() == {"updated": {}}
    db.commit.assert_not_awaited()


# ───────── доступ ─────────


def test_page_and_api_are_not_in_the_public_allowlists():
    """Страница управляет тем, что выходит на стены районов, — она обязана быть
    закрыта. Гейт secure by default, поэтому проверяем не наличие зависимости,
    а отсутствие пути в allowlist'ах: именно попадание туда сделало бы ручку
    публичной молча, без единой правки в самом роутере."""
    from middleware import auth_gate

    for path in ("/themes", "/api/theme-quotas", "/api/theme-quotas/"):
        assert path not in auth_gate.PUBLIC_EXACT, f"{path} попал в PUBLIC_EXACT"
        matched = [p for p in auth_gate.PUBLIC_PREFIXES if path.startswith(p)]
        assert not matched, f"{path} накрыт публичным префиксом: {matched}"


# ───────── GET против настоящей базы ─────────


@pytest_asyncio.fixture()
async def db():
    """Живая in-memory база: GET читает JSON-поле вердикта выражением
    ``verdict['theme'].as_string()``, и мок такую ошибку не поймал бы —
    расхождение диалектов вылезло бы только на проде."""
    engine = create_async_engine("sqlite+aiosqlite://")
    tables = [
        ClassifierTheme.__table__,
        ClassifierThemeAlias.__table__,
        ContentClassification.__table__,
        PublishedPost.__table__,
    ]
    async with engine.begin() as conn:
        await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=tables))
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        yield session
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_themes_reads_plan_candidates_and_facts(db, monkeypatch):
    monkeypatch.delenv("CLASSIFIER_THEME_QUOTA_ENABLED", raising=False)
    db.add(ClassifierTheme(name="новости", position=1, share_percent=50, description="Что было"))
    db.add(ClassifierTheme(name="спорт", position=2, share_percent=20))
    db.add(ClassifierTheme(name="мусор", position=3, is_service=True))
    for i in range(3):
        db.add(
            ContentClassification(
                lip=f"100_{i}",
                region_code="mi",
                model="test",
                verdict={"theme": "новости", "action": "publish"},
            )
        )
    db.add(
        ContentClassification(
            lip="100_9",
            region_code="mi",
            model="test",
            verdict={"theme": "спорт", "action": "publish"},
        )
    )
    db.add(
        PublishedPost(lip="100_1", region_code="mi", wave_theme="novost", verdict_theme="новости")
    )
    await db.commit()

    data = await tq.get_themes(db)
    by_name = {t["theme"]: t for t in data["themes"]}

    assert by_name["новости"]["candidates_count"] == 3
    assert by_name["новости"]["candidates_pct"] == 75.0
    assert by_name["новости"]["published_pct"] == 100.0
    assert by_name["спорт"]["candidates_pct"] == 25.0
    # Служебная тема в сумму плана не входит: 50 + 20, «мусор» без доли.
    assert data["planned_sum"] == 70.0
    assert data["quota_enabled"] is False


@pytest.mark.asyncio
async def test_unreachable_flag_marks_a_goal_the_cap_cannot_reach(db):
    # Спорт: план 20%, кандидатов 25% → достижимо. Культура: план 40% при 25% →
    # потолок не поднимет, рычаг в расписании и пуле источников.
    db.add(ClassifierTheme(name="спорт", position=1, share_percent=20))
    db.add(ClassifierTheme(name="культура", position=2, share_percent=40))
    for i in range(3):
        db.add(
            ContentClassification(
                lip=f"200_{i}",
                region_code="mi",
                model="test",
                verdict={"theme": "спорт", "action": "publish"},
            )
        )
    db.add(
        ContentClassification(
            lip="200_9",
            region_code="mi",
            model="test",
            verdict={"theme": "культура", "action": "publish"},
        )
    )
    await db.commit()

    by_name = {t["theme"]: t for t in (await tq.get_themes(db))["themes"]}
    assert by_name["спорт"]["unreachable"] is False
    assert by_name["культура"]["unreachable"] is True


@pytest.mark.asyncio
async def test_candidates_ignore_non_publish_verdicts(db):
    db.add(ClassifierTheme(name="новости", position=1))
    db.add(
        ContentClassification(
            lip="300_1",
            region_code="mi",
            model="t",
            verdict={"theme": "новости", "action": "delete"},
        )
    )
    await db.commit()

    by_name = {t["theme"]: t for t in (await tq.get_themes(db))["themes"]}
    assert by_name["новости"]["candidates_count"] == 0
    # Знаменателя нет — «мерить не по чему», а не «ноль процентов».
    assert by_name["новости"]["candidates_pct"] is None


# ───────── шаблон ─────────


def test_template_renders_with_the_real_engine():
    """Шаблон наследует base.html и объявляет блок ``nav_themes``. Если блок в
    base.html не завести, Jinja промолчит на импорте приложения и упадёт только
    при первом заходе оператора — то есть на проде."""
    from pathlib import Path

    from fastapi.templating import Jinja2Templates

    class _FakeURL:
        path = "/themes"
        query = ""

    class _FakeRequest:
        url = _FakeURL()
        headers: dict = {}
        query_params: dict = {}
        cookies: dict = {}

    base = Path(__file__).resolve().parents[2]
    templates = Jinja2Templates(directory=str(base / "web" / "templates"))
    html = templates.get_template("themes.html").render(request=_FakeRequest())

    assert "/static/js/themes.js" in html
    assert "themes-body" in html
    # Обещание страницы: потолок не создаёт посты, и об этом сказано явно.
    assert "не умеет создать" in html


# ───────── сторож диалекта ─────────


def test_candidates_query_groups_by_a_plain_column_on_postgres():
    """Регресс на прод-ошибку 2026-08-30 (найдена при выкатке PR #570).

    Прямая группировка по ``verdict['theme']`` работает на SQLite и падает на
    Postgres: SQLAlchemy подставляет РАЗНЫЕ bind-параметры под один литерал
    'theme' в SELECT и в GROUP BY, Postgres сравнивает выражения синтаксически и
    не признаёт их одним — «column verdict must appear in the GROUP BY clause».

    Интеграционный тест выше это не ловит: он гоняет живую базу, но SQLite, а
    там правило мягче. Поэтому сторож смотрит на СКОМПИЛИРОВАННЫЙ под Postgres
    SQL и требует, чтобы в GROUP BY стояла простая колонка, а не JSON-выражение.
    """
    from sqlalchemy.dialects import postgresql

    from web.api.theme_quotas import candidate_counts_stmt

    sql = str(candidate_counts_stmt(days=7).compile(dialect=postgresql.dialect()))
    group_by = sql.split("GROUP BY", 1)[1]
    assert "->>" not in group_by, (
        "в GROUP BY попало JSON-выражение — Postgres отвергнет запрос: " + group_by
    )
    assert "verdict" not in group_by, "GROUP BY обязан ссылаться на колонку подзапроса"
