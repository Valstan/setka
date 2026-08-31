"""Вход владельца в кабинет клиента: права, журнал, изоляция.

Изоляционные утверждения гоняются настоящим SQL (in-memory БД из conftest) —
фейковая сессия не умеет краснеть на неверном WHERE.

Главный тест здесь — ``test_non_owner_is_refused_not_ignored``. Если бы чужой
``as_client`` просто игнорировался, всё остальное осталось бы зелёным: клиент
получал бы свою карточку, тесты бы это подтвердили, и дыра открылась бы при
первой же правке вызывающего кода. Отказ обязан быть громким.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from database.models import AdClient, AdInteraction
from database.models_extended import RadarUser
from modules.ad_cabinet import impersonation

OWNER_VK_ID = 20002978  # дефолт SETKA_OWNER_VK_IDS


class _Req:
    """Минимальный двойник Request: только то, что читает impersonation."""

    def __init__(self, *, as_client=None, method="GET", path="/api/advertiser/me"):
        self.query_params = {} if as_client is None else {"as_client": str(as_client)}
        self.method = method
        self.url = SimpleNamespace(path=path)


async def _user(session, *, login=None, vk_user_id=None, role="radar"):
    u = RadarUser(login=login, role=role, vk_user_id=vk_user_id)
    session.add(u)
    await session.flush()
    return u


async def _client(session, *, name="Клиент", radar_user_id=None):
    c = AdClient(name=name, radar_user_id=radar_user_id)
    session.add(c)
    await session.flush()
    return c


# ───────── кто такой владелец ─────────


@pytest.mark.asyncio
async def test_owner_recognised_by_vk_id(db_session):
    """ВК-вход владельца — роль radar, и это нормально (см. _is_owner)."""
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID, role="radar")
    assert impersonation.is_owner(owner) is True


@pytest.mark.asyncio
async def test_owner_recognised_by_login(db_session):
    owner = await _user(db_session, login="valstan", role="operator")
    assert impersonation.is_owner(owner) is True


@pytest.mark.asyncio
async def test_plain_advertiser_is_not_owner(db_session):
    user = await _user(db_session, login="petrov", role="advertiser")
    assert impersonation.is_owner(user) is False


@pytest.mark.asyncio
async def test_owner_login_without_operator_role_is_not_owner(db_session):
    """Регистрозависимость логина не должна давать владельческие права.

    Публичная регистрация ставит роль ``radar``; логин-ветка требует
    ``operator`` именно поэтому.
    """
    impostor = await _user(db_session, login="valstan", role="radar")
    assert impersonation.is_owner(impostor) is False


# ───────── разбор параметра ─────────


def test_requested_client_id_parses():
    assert impersonation.requested_client_id(_Req(as_client=11)) == 11


def test_requested_client_id_absent_is_none():
    assert impersonation.requested_client_id(_Req()) is None


@pytest.mark.parametrize("raw", ["abc", "", "0", "-3", "  "])
def test_requested_client_id_garbage_is_none(raw):
    """Мусор — это «параметра нет», а не «кабинет №0»."""
    req = _Req()
    req.query_params = {"as_client": raw}
    assert impersonation.requested_client_id(req) is None


# ───────── права ─────────


@pytest.mark.asyncio
async def test_owner_gets_requested_client(db_session):
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID)
    card = await _client(db_session, name="Пекарня")

    target, impersonated = await impersonation.resolve(db_session, owner, _Req(as_client=card.id))

    assert impersonated is True
    assert target is not None and target.id == card.id


@pytest.mark.asyncio
async def test_non_owner_is_refused_not_ignored(db_session):
    """Чужой ``as_client`` → 403. Молчаливое игнорирование недопустимо."""
    intruder = await _user(db_session, login="petrov", role="advertiser")
    victim = await _client(db_session, name="Чужая карточка")

    with pytest.raises(HTTPException) as e:
        await impersonation.resolve(db_session, intruder, _Req(as_client=victim.id))
    assert e.value.status_code == 403


@pytest.mark.asyncio
async def test_owner_asking_for_missing_client_gets_404(db_session):
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID)
    with pytest.raises(HTTPException) as e:
        await impersonation.resolve(db_session, owner, _Req(as_client=999_999))
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_request_without_param_is_untouched(db_session):
    """Обычный запрос клиента импресонацией не считается — путь прежний."""
    user = await _user(db_session, login="petrov", role="advertiser")
    target, impersonated = await impersonation.resolve(db_session, user, _Req())
    assert (target, impersonated) == (None, False)


@pytest.mark.asyncio
async def test_owner_without_param_is_untouched(db_session):
    """Владелец без параметра тоже идёт обычным путём — не «всегда чужой»."""
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID)
    target, impersonated = await impersonation.resolve(db_session, owner, _Req())
    assert (target, impersonated) == (None, False)


# ───────── журнал ─────────


async def _kinds(session):
    rows = (await session.execute(select(AdInteraction))).scalars().all()
    return [(r.kind, r.actor, r.client_id) for r in rows]


@pytest.mark.asyncio
async def test_entering_cabinet_is_logged(db_session):
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID)
    card = await _client(db_session)

    await impersonation.resolve(db_session, owner, _Req(as_client=card.id))
    await db_session.flush()

    assert (impersonation.KIND_ENTER, "owner", card.id) in await _kinds(db_session)


@pytest.mark.asyncio
async def test_mutation_is_logged_as_action(db_session):
    """Полный доступ означает действия ОТ ИМЕНИ клиента — их надо отличать."""
    owner = await _user(db_session, vk_user_id=OWNER_VK_ID)
    card = await _client(db_session)

    await impersonation.resolve(
        db_session,
        owner,
        _Req(as_client=card.id, method="POST", path="/api/advertiser/orders"),
    )
    await db_session.flush()

    kinds = await _kinds(db_session)
    assert (impersonation.KIND_ACTION, "owner", card.id) in kinds
    assert (impersonation.KIND_ENTER, "owner", card.id) not in kinds


# ───────── канонический редирект не должен глотать параметр ─────────


@pytest.mark.asyncio
async def test_canonical_redirect_keeps_query(monkeypatch):
    """Иначе `?as_client=` терялся бы по дороге — молча и незаметно.

    Отказ этого класса не виден ни в логах, ни в коде страницы: владелец
    открывал бы СВОЙ кабинет вместо чужого и считал, что фича не работает.
    """
    import main
    from modules.radar_id import vk_upstream

    monkeypatch.setattr(
        vk_upstream, "ad_cabinet_canonical_redirect", lambda _h: "https://example.test/cabinet"
    )
    req = SimpleNamespace(url=SimpleNamespace(hostname="other.test", query="as_client=11"))

    resp = await main.advertiser_cabinet_page(req)

    assert resp.status_code == 302
    assert resp.headers["location"] == "https://example.test/cabinet?as_client=11"


@pytest.mark.asyncio
async def test_canonical_redirect_without_query_unchanged(monkeypatch):
    """Пустой query не должен превращаться в висячий «?»."""
    import main
    from modules.radar_id import vk_upstream

    monkeypatch.setattr(
        vk_upstream, "ad_cabinet_canonical_redirect", lambda _h: "https://example.test/cabinet"
    )
    req = SimpleNamespace(url=SimpleNamespace(hostname="other.test", query=""))

    resp = await main.advertiser_cabinet_page(req)

    assert resp.headers["location"] == "https://example.test/cabinet"


@pytest.mark.asyncio
async def test_refused_attempt_writes_nothing(db_session):
    """Отказ не должен оставлять запись «владелец заходил»."""
    intruder = await _user(db_session, login="petrov", role="advertiser")
    card = await _client(db_session)

    with pytest.raises(HTTPException):
        await impersonation.resolve(db_session, intruder, _Req(as_client=card.id))
    await db_session.flush()

    assert await _kinds(db_session) == []
