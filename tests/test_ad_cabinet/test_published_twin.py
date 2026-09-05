"""Инцидент 2026-09-05: отложка из предложки выходит под НОВЫМ id.

- проверка выхода (wall.getById) идёт user-токеном, а не community (ошибка 27);
- pick_twin: подпись + окно времени, либо начало текста; чужие/старые — мимо;
- resolve_publication: id исчез → двойник найден → строка получает настоящий id;
  обычный пост (kind=post) двойника не ищет;
- реконсилер фиксирует такую строку с настоящим id;
- диспетчер репостит с настоящего id, а не со старой отложки.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from database.models import AdClient, AdPublication, AdRequest, AdScheduledPost
from modules.ad_cabinet import publish_reconciler as pr
from modules.ad_cabinet import repost_dispatcher as rd

PUB = datetime(2026, 9, 5, 10, 0)  # МСК
OWNER = -158787639
SIGNER = 139799739


def _utc_ts(dt_msk: datetime) -> int:
    import calendar

    return calendar.timegm((dt_msk - timedelta(hours=3)).timetuple())


# ───────── токен проверки ─────────


def test_checker_prefers_user_token(monkeypatch):
    used = []

    class _VkApi:
        def __init__(self, token):
            used.append(token)
            self._t = token

        def get_api(self):
            api = types.SimpleNamespace()
            api.wall = types.SimpleNamespace(
                getById=lambda posts: [{"post_type": "post"}],
            )
            return api

    monkeypatch.setitem(sys.modules, "vk_api", types.SimpleNamespace(VkApi=_VkApi))
    checker = pr._build_default_checker("USER", {158787639: "COMMUNITY"})
    assert checker(OWNER, 1) is True
    assert used == ["USER"]


# ───────── чистый подбор двойника ─────────


def _wall():
    return [
        {"id": 78304, "post_type": "post", "date": _utc_ts(PUB + timedelta(hours=1, minutes=43))},
        {"id": 78300, "post_type": "post", "date": _utc_ts(PUB + timedelta(minutes=6))},
        {
            "id": 78299,
            "post_type": "post",
            "date": _utc_ts(PUB),
            "signer_id": SIGNER,
            "text": "😱 «Если подам на банкротство — потеряю работу»...",
        },
        {"id": 78298, "post_type": "post", "date": _utc_ts(PUB - timedelta(hours=2))},
        {
            "id": 78294,
            "post_type": "postpone",
            "date": _utc_ts(PUB + timedelta(days=4)),
            "signer_id": SIGNER,
        },
    ]


def test_pick_twin_by_signer_and_window():
    assert pr.pick_twin(_wall(), signer_id=SIGNER, text="другой текст", publish_date=PUB) == 78299
    # подпись есть, но пост вне окна ±45 мин — не берём
    assert (
        pr.pick_twin(_wall(), signer_id=SIGNER, text="x", publish_date=PUB + timedelta(hours=3))
        is None
    )
    # без подписи — по началу текста (пробелы/регистр не важны)
    assert (
        pr.pick_twin(
            _wall(),
            signer_id=None,
            text="😱  «если подам на банкротство — потеряю работу»",
            publish_date=PUB,
        )
        == 78299
    )
    # ни подписи, ни текста — ничего
    assert pr.pick_twin(_wall(), signer_id=None, text="", publish_date=PUB) is None
    assert pr.pick_twin(_wall(), signer_id=SIGNER, text="x", publish_date=None) is None


# ───────── resolve_publication ─────────


async def _seed(session, *, kind="suggested", with_request=True):
    client = AdClient(id=10, author_vk_id=SIGNER, name="Анна")
    session.add(client)
    if with_request:
        session.add(
            AdRequest(
                id=1103147,
                community_vk_id=OWNER,
                vk_post_id=78276,
                author_vk_id=SIGNER,
                status="published",
            )
        )
    row = AdScheduledPost(
        id=8,
        kind=kind,
        community_vk_id=OWNER,
        text="😱 «Если подам на банкротство — потеряю работу»...",
        publish_date=PUB,
        status="scheduled",
        vk_postponed_post_id=78293,
        client_id=10,
        price=200,
        signed=True,
        source_ad_request_id=1103147 if with_request else None,
    )
    session.add(row)
    await session.flush()
    return row


@pytest.mark.asyncio
async def test_resolve_publication_uses_twin_when_id_vanished(db_session):
    row = await _seed(db_session)
    calls = []

    def find_twin(owner_id, signer_id, text, publish_date):
        calls.append((owner_id, signer_id, publish_date))
        return pr.pick_twin(_wall(), signer_id=signer_id, text=text, publish_date=publish_date)

    state = await pr.resolve_publication(
        db_session, row, is_published=lambda o, p: None, find_twin=find_twin
    )
    assert state is True and row.vk_postponed_post_id == 78299
    assert calls == [(OWNER, SIGNER, PUB)]

    # id жив и VK говорит «ещё отложка» — двойника не ищем
    calls.clear()
    row.vk_postponed_post_id = 78293
    state = await pr.resolve_publication(
        db_session, row, is_published=lambda o, p: False, find_twin=find_twin
    )
    assert state is False and calls == [] and row.vk_postponed_post_id == 78293


@pytest.mark.asyncio
async def test_resolve_publication_ignores_twins_for_plain_posts(db_session):
    row = await _seed(db_session, kind="post", with_request=False)
    state = await pr.resolve_publication(
        db_session, row, is_published=lambda o, p: None, find_twin=lambda *a: 78299
    )
    assert state is None and row.vk_postponed_post_id == 78293


@pytest.mark.asyncio
async def test_reconcile_records_twin_id(db_session):
    await _seed(db_session)
    await db_session.commit()

    class _F:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    out = await pr.run_reconcile(
        session_factory=_F(),
        is_published=lambda o, p: None,
        find_twin=lambda o, s, t, d: pr.pick_twin(_wall(), signer_id=s, text=t, publish_date=d),
        now=PUB + timedelta(minutes=30),
    )
    assert out["reconciled"] == 1
    pub = (await db_session.execute(select(AdPublication))).scalar_one()
    assert pub.vk_post_id == 78299 and pub.scheduled_post_id == 8


@pytest.mark.asyncio
async def test_dispatcher_reposts_from_twin_id(db_session):
    orig = await _seed(db_session)
    rep = AdScheduledPost(
        id=9,
        kind="repost",
        source_post_id=orig.id,
        community_vk_id=-179203620,
        text=orig.text,
        publish_date=PUB,
        status="scheduled",
        next_attempt_at=PUB,
        client_id=10,
        price=200,
    )
    db_session.add(rep)
    await db_session.commit()

    class _F:
        def __call__(self):
            return self

        async def __aenter__(self):
            return db_session

        async def __aexit__(self, *a):
            return False

    class _Pub:
        def __init__(self):
            self.reposts = []

        async def publish_repost(self, *, group_id, source_owner_id, source_post_id, message=""):
            self.reposts.append((group_id, source_owner_id, source_post_id))
            return {"success": True, "post_id": 25904}

    publisher = _Pub()

    async def factory(gid):
        return publisher

    out = await rd.run_repost_dispatch(
        session_factory=_F(),
        publisher_factory=factory,
        is_published=lambda o, p: None,
        find_twin=lambda o, s, t, d: pr.pick_twin(_wall(), signer_id=s, text=t, publish_date=d),
        interval=0,
        now=PUB + timedelta(minutes=5),
        alert=lambda *a: None,
    )
    assert out["published"] == 1 and out["waiting"] == 0
    assert publisher.reposts == [(-179203620, OWNER, 78299)]
    await db_session.refresh(orig)
    assert orig.status == "published" and orig.vk_postponed_post_id == 78299
