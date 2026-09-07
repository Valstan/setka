"""Отмена размещения из ВК-бота — и один движок отмены на бот и веб-кабинет.

Аудит кабинетов 07.09: бот умел заказать, но не умел отменить. Клиент, который
всё делает в переписке, упирался — отмена жила только в веб-кабинете
(`POST /api/advertiser/posts/{id}/cancel`), и в списке «Мои посты» об этом не
было сказано ни слова.

Чинилось не копированием эндпойнта в бота. Логика отмены — три неочевидных
условия подряд (блокировка строки от реконсилера, терминальные статусы, снятие
из VK-отложки), и две её реализации разошлись бы молча. Поэтому она вынесена в
``client_orders.cancel_own_post``, а бот и веб её зовут.

Что охраняется здесь:

* отмена возвращает слот в пакет и пишет в журнал;
* чужой пост не отменяется, и «чужой» неотличим от «нет такого» — иначе
  перебором id можно было бы узнавать о чужих размещениях;
* терминальные статусы не трогаются (``failed`` уже вернул слот — второй
  возврат вернул бы его дважды);
* кнопка вешается на ЗАКАЗ и снимает все его строки;
* отказ ВК не превращается в «отменил»: статус остаётся, владелец уведомлён.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from database.models import AdClient, AdInteraction, AdScheduledPost, Region
from modules.ad_cabinet import client_orders
from modules.ad_cabinet.vk_bot import dialog

NOW = datetime(2026, 9, 7, 12, 0)


async def _client(session, vk_id=777):
    c = AdClient(author_vk_id=vk_id, stage="detected", trusted=False)
    session.add(c)
    await session.flush()
    return c


async def _region(session, name, gid):
    r = Region(name=name, code=name.lower(), vk_group_id=gid, is_active=True)
    session.add(r)
    await session.flush()
    return r


async def _post(session, client, region, *, status="scheduled", order_ref="ord-1", vk_id=None):
    p = AdScheduledPost(
        client_id=client.id,
        community_vk_id=region.vk_group_id,
        region_id=region.id,
        text="реклама",
        publish_date=datetime(2026, 9, 10, 10, 0),
        status=status,
        order_ref=order_ref,
        vk_postponed_post_id=vk_id,
        price=350,
    )
    session.add(p)
    await session.flush()
    return p


class TestEngine:
    @pytest.mark.asyncio
    async def test_pending_post_is_cancelled_and_logged(self, db_session):
        c = await _client(db_session)
        r = await _region(db_session, "Уржум", -101)
        p = await _post(db_session, c, r, status="pending")

        res = await client_orders.cancel_own_post(db_session, c, p.id, source="vk_bot")

        assert res["ok"] is True and res["reason"] is None
        assert p.status == "cancelled"
        logged = (
            (await db_session.execute(__import__("sqlalchemy").select(AdInteraction)))
            .scalars()
            .all()
        )
        assert any(i.kind == "cancelled" for i in logged)

    @pytest.mark.asyncio
    async def test_someone_elses_post_is_indistinguishable_from_missing(self, db_session):
        """Гвоздь: чужой пост и несуществующий отвечают одинаково.

        Разные ответы позволили бы перебором id узнать, что у соседа есть
        размещение на такую-то дату.
        """
        mine = await _client(db_session, vk_id=1)
        other = await _client(db_session, vk_id=2)
        r = await _region(db_session, "Нолинск", -102)
        foreign = await _post(db_session, other, r, status="pending")

        got_foreign = await client_orders.cancel_own_post(db_session, mine, foreign.id)
        got_missing = await client_orders.cancel_own_post(db_session, mine, 999999)

        assert got_foreign["reason"] == got_missing["reason"] == "not_found"
        assert foreign.status == "pending", "чужой пост не тронут"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("status", ["published", "cancelled", "rejected", "failed"])
    async def test_terminal_statuses_are_left_alone(self, db_session, status):
        c = await _client(db_session)
        r = await _region(db_session, "Суна", -103)
        p = await _post(db_session, c, r, status=status)

        res = await client_orders.cancel_own_post(db_session, c, p.id)

        assert res["reason"] == "terminal"
        assert p.status == status, "failed уже вернул слот — второй возврат удвоил бы его"


class TestBotButtons:
    def test_button_is_attached_to_the_order_not_the_row(self):
        """Заказ на три района — одна кнопка, а не три.

        В списке строки внутри заказа не пронумерованы: построчные кнопки не с
        чем было бы сопоставить глазами.
        """
        views = [
            dialog.client_posts.PostView(
                id=i,
                order_ref="ord-1",
                region_name=f"Р{i}",
                community_vk_id=-100 - i,
                publish_date=datetime(2026, 9, 10, 10, 0),
                status="scheduled",
                kind="direct",
                price=350,
                moderation_comment=None,
                error_message=None,
                image_count=0,
                vk_post_url=None,
                created_at=datetime(2026, 9, 7, 9, 0),
            )
            for i in (1, 2, 3)
        ]
        # Разбираем структуру, а не текст: payload внутри клавиатуры — это
        # JSON внутри JSON, и поиск подстроки ловил бы экранирование, а не смысл.
        kb = json.loads(dialog.posts_keyboard(views))
        payloads = [json.loads(b["action"]["payload"]) for row in kb["buttons"] for b in row]
        cancels = [p for p in payloads if p.get("cmd") == dialog.CMD_CANCEL_POST]
        assert len(cancels) == 1, "одна кнопка на заказ, а не на строку"
        assert cancels[0]["o"] == "ord-1"

    def test_terminal_orders_get_no_button(self):
        views = [
            dialog.client_posts.PostView(
                id=1,
                order_ref="ord-x",
                region_name="Уржум",
                community_vk_id=-101,
                publish_date=None,
                status="published",
                kind="direct",
                price=350,
                moderation_comment=None,
                error_message=None,
                image_count=0,
                vk_post_url=None,
                created_at=datetime(2026, 9, 7, 9, 0),
            )
        ]
        assert dialog.posts_keyboard(views) == dialog.MAIN_KEYBOARD


class TestBotFlow:
    @pytest.mark.asyncio
    async def test_cancel_button_cancels_whole_order(self, db_session):
        c = await _client(db_session, vk_id=500)
        r1 = await _region(db_session, "Уржум", -201)
        r2 = await _region(db_session, "Нолинск", -202)
        p1 = await _post(db_session, c, r1, status="pending", order_ref="ord-7")
        p2 = await _post(db_session, c, r2, status="pending", order_ref="ord-7")

        replies, state, events = await dialog.handle(
            db_session,
            dialog.Incoming(peer_id=500, payload={"cmd": dialog.CMD_CANCEL_POST, "o": "ord-7"}),
            None,
            submit=None,
            now_msk=NOW,
        )

        assert p1.status == "cancelled" and p2.status == "cancelled"
        assert "Отменил 2" in replies[0][0]
        assert "order_cancelled" in events
        assert state is None

    @pytest.mark.asyncio
    async def test_nothing_to_cancel_is_state_not_error(self, db_session):
        """Заказ мог выйти, пока клиент смотрел на экран — это не отказ."""
        c = await _client(db_session, vk_id=500)
        r = await _region(db_session, "Суна", -203)
        await _post(db_session, c, r, status="published", order_ref="ord-9")

        replies, _state, events = await dialog.handle(
            db_session,
            dialog.Incoming(peer_id=500, payload={"cmd": dialog.CMD_CANCEL_POST, "o": "ord-9"}),
            None,
            submit=None,
            now_msk=NOW,
        )

        assert "Отменять нечего" in replies[0][0]
        assert "order_cancelled" not in events

    @pytest.mark.asyncio
    async def test_payload_without_id_does_not_crash_the_dialog(self, db_session):
        await _client(db_session, vk_id=500)
        replies, _state, events = await dialog.handle(
            db_session,
            dialog.Incoming(peer_id=500, payload={"cmd": dialog.CMD_CANCEL_POST}),
            None,
            submit=None,
            now_msk=NOW,
        )
        assert "Мои посты" in replies[0][0]
        assert "order_cancelled" not in events
