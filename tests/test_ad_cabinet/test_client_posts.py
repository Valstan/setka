"""«📋 Мои посты» в ВК-боте (modules/ad_cabinet/client_posts) — Этап 5, PR-3.

Настоящая in-memory БД. Что охраняется: группировка по order_ref, имена районов,
ссылка из ad_publications первична (fallback — vk_postponed_post_id только для
published), причина отказа и ошибка показываются, предложка/репост помечены,
длинный список режется под лимит ВК, пустой список — подсказка, кнопка в меню
работает из любого шага.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from database.models import AdClient, AdPublication, AdScheduledPost, Region
from modules.ad_cabinet import client_posts as cp
from modules.ad_cabinet.vk_bot import dialog

NOW = datetime(2026, 9, 2, 12, 0)


async def _client(session, **kw):
    c = AdClient(name=kw.pop("name", "К"), **kw)
    session.add(c)
    await session.flush()
    return c


async def _region(session, name, gid):
    r = Region(name=name, code=name.lower(), vk_group_id=gid, is_active=True)
    session.add(r)
    await session.flush()
    return r


def _post(client, region, *, day=0, **kw):
    fields = dict(
        client_id=client.id,
        community_vk_id=region.vk_group_id if region else -999,
        region_id=region.id if region else None,
        text="t",
        publish_date=NOW + timedelta(days=day),
        status="scheduled",
        price=Decimal("350"),
        created_at=NOW - timedelta(hours=3),
    )
    fields.update(kw)
    return AdScheduledPost(**fields)


@pytest.mark.asyncio
async def test_list_and_render_group_by_order(db_session):
    c = await _client(db_session)
    a = await _region(db_session, "Арбаж", -1)
    b = await _region(db_session, "Уржум", -2)
    p1 = _post(c, a, order_ref="o1", status="published", vk_postponed_post_id=5)
    p2 = _post(c, b, order_ref="o1", status="published", vk_postponed_post_id=6)
    p3 = _post(c, a, day=1, order_ref="o2", status="rejected", moderation_comment="реклама МФО")
    p4 = _post(c, b, day=1, order_ref="o2", status="failed", error_message="нет user-токена")
    p5 = _post(c, None, day=2, kind="repost", price=Decimal("0"), image_names=["a.jpg"])
    db_session.add_all([p1, p2, p3, p4, p5])
    await db_session.flush()
    db_session.add(
        AdPublication(
            client_id=c.id,
            community_vk_id=-1,
            vk_post_id=77,
            scheduled_post_id=p1.id,
            status="published",
        )
    )
    await db_session.flush()

    views, hidden, exact = await cp.list_for_client(db_session, c.id)
    assert hidden == 0 and exact is True
    assert [v.id for v in views] == [p5.id, p4.id, p3.id, p2.id, p1.id]
    by_id = {v.id: v for v in views}
    assert by_id[p1.id].vk_post_url == "https://vk.com/wall-1_77"  # публикация первична
    assert by_id[p2.id].vk_post_url == "https://vk.com/wall-2_6"  # fallback для published
    assert by_id[p3.id].vk_post_url is None and by_id[p1.id].region_name == "Арбаж"
    assert by_id[p5.id].region_name == "сообщество -999" and by_id[p5.id].image_count == 1

    chunks = cp.render(views)
    assert len(chunks) == 1
    text = chunks[0]
    assert text.startswith("📋 Мои посты")
    assert text.count("Заказ") == 3  # o2, o1 и одиночный репост
    assert "2 сообщества, 700 ₽" in text and "1 сообщество, в счёт пакета" in text
    assert "Арбаж — 02.09 12:00 — вышел" in text and "https://vk.com/wall-1_77" in text
    assert "отклонён" in text and "причина: реклама МФО" in text
    assert "ошибка" in text and "ошибка: нет user-токена" in text
    assert "(репост) 📷1" in text
    assert text.index("Уржум — 03.09") < text.index("Арбаж — 02.09")  # новые заказы выше


def test_render_empty_and_chunks():
    assert cp.render([]) == ["Постов пока нет — нажмите «🛒 Заказать пост»."]
    views = [
        cp.PostView(
            id=i,
            order_ref=f"o{i}",
            region_name="Район с длинным названием " * 3,
            community_vk_id=-1,
            publish_date=NOW,
            status="scheduled",
            kind="post",
            price=350.0,
            moderation_comment=None,
            error_message=None,
            image_count=0,
            vk_post_url=None,
            created_at=NOW,
        )
        for i in range(60)
    ]
    chunks = cp.render(views, max_orders=60, msg_max=600)
    assert len(chunks) > 1 and all(len(ch) <= 600 for ch in chunks)
    assert chunks[0].startswith("📋 Мои посты")
    short = cp.render(views)  # потолок 10 заказов + хвост со ссылкой на кабинет
    assert "…и ещё 50 заказов" in short[-1] and cp.CABINET_URL in short[-1]


@pytest.mark.asyncio
async def test_menu_button_shows_posts(db_session):
    c = await _client(db_session, author_vk_id=500)
    a = await _region(db_session, "Арбаж", -1)
    db_session.add(_post(c, a, order_ref="o1", status="pending"))
    await db_session.flush()

    async def submit(*a, **k):
        raise AssertionError("submit не должен вызываться")

    replies, state, _ = await dialog.handle(
        db_session,
        dialog.Incoming(peer_id=500, payload={"cmd": "posts"}),
        {"step": "order_when", "draft": {"x": 1}},
        submit=submit,
        now_msk=NOW,
    )
    assert state is None and replies[0][1] == dialog.MAIN_KEYBOARD
    assert "Мои посты" in replies[0][0] and "на одобрении" in replies[0][0]
    assert dialog.Incoming(peer_id=1, text="📋 Мои посты").command() == "posts"


@pytest.mark.asyncio
async def test_window_never_cuts_an_order_in_half(db_session):
    """Заказ на всю сеть не показывается половиной: окно берётся по заказам.

    Иначе шапка печатала бы «22 сообщества, 2 895 ₽» у заказа на 38 сообществ
    и 5 000 ₽ — заниженные число и сумму, да ещё без признака обрезки.
    """
    c = await _client(db_session)
    regions = [await _region(db_session, f"Район {i:02d}", -(100 + i)) for i in range(38)]
    for order, day in (("A", 0), ("B", 1)):
        db_session.add_all(
            [_post(c, r, day=day, order_ref=order, price=Decimal("100")) for r in regions]
        )
    await db_session.flush()

    views, hidden, exact = await cp.list_for_client(db_session, c.id, max_orders=1)
    assert len(views) == 38 and {v.order_ref for v in views} == {"B"}
    assert hidden == 1 and exact is True
    text = "\n".join(cp.render(views, hidden=hidden, hidden_exact=exact))
    assert "38 сообществ, 3 800 ₽" in text and "22 сообщества" not in text
    assert "…и ещё 1 заказ —" in text and cp.CABINET_URL in text

    # обе группы влезают — скрытых нет
    views, hidden, _ = await cp.list_for_client(db_session, c.id, max_orders=10)
    assert len(views) == 76 and hidden == 0


@pytest.mark.asyncio
async def test_hidden_count_is_honest_and_marked_when_scan_capped(db_session):
    c = await _client(db_session)
    a = await _region(db_session, "Арбаж", -1)
    db_session.add_all([_post(c, a, day=i, order_ref=f"o{i}") for i in range(15)])
    await db_session.flush()

    views, hidden, exact = await cp.list_for_client(db_session, c.id)
    assert len(views) == 10 and hidden == 5 and exact is True
    assert "…и ещё 5 заказов" in "\n".join(cp.render(views, hidden=hidden))

    # скан упёрся в потолок — счёт помечается как неточный
    views, hidden, exact = await cp.list_for_client(db_session, c.id, max_orders=2, scan_limit=6)
    assert exact is False and hidden == 4
    assert "…и ещё 4+ заказа" in "\n".join(cp.render(views, hidden=hidden, hidden_exact=exact))


@pytest.mark.asyncio
async def test_rows_without_order_ref_are_their_own_orders(db_session):
    c = await _client(db_session)
    a = await _region(db_session, "Арбаж", -1)
    db_session.add_all([_post(c, a, day=i) for i in range(3)])
    await db_session.flush()
    views, hidden, _ = await cp.list_for_client(db_session, c.id, max_orders=2)
    assert len(views) == 2 and hidden == 1


# ───────── находки ревью PR-3 ─────────


def _views(n, *, order_ref="o1", comment=None, status="scheduled", name="Район"):
    return [
        cp.PostView(
            id=i,
            order_ref=order_ref,
            region_name=f"{name} {i}",
            community_vk_id=-1,
            publish_date=NOW,
            status=status,
            kind="post",
            price=350.0,
            moderation_comment=comment,
            error_message=None,
            image_count=0,
            vk_post_url=None,
            created_at=NOW,
        )
        for i in range(n)
    ]


def test_huge_single_order_never_sends_bare_header():
    """Отказ всего заказа на 38 районов с причиной: шапка едет с блоком, обрыв — честный."""
    comment = "Уберите номер телефона из текста и оформите как объявление, а не как новость"
    views = _views(38, status="rejected", comment=comment, name="Кирово-Чепецкий район")
    chunks = cp.render(views)
    assert all(len(ch) <= cp.MSG_MAX for ch in chunks)
    assert chunks[0].startswith(cp.HEAD) and chunks[0].strip() != cp.HEAD
    joined = "\n".join(chunks)
    assert "список обрезан" in joined and cp.CABINET_URL in joined


def test_plural_forms_in_header_and_tail():
    for n, word in (
        (1, "1 сообщество"),
        (2, "2 сообщества"),
        (21, "21 сообщество"),
        (25, "25 сообществ"),
    ):
        assert word in cp.render_group(_views(n)), (n, word)
    many = []
    for i in range(11):
        many += _views(1, order_ref=f"o{i}")
    tail = cp.render(many)[-1]
    assert "…и ещё 1 заказ —" in tail


def test_small_block_after_overflow_keeps_header_once():
    views = _views(38, status="rejected", comment="причина " * 20) + _views(1, order_ref="o2")
    chunks = cp.render(views)
    assert sum(ch.count(cp.HEAD) for ch in chunks) == 1
    assert any("Район 0" in ch for ch in chunks[-1:]) or len(chunks) >= 2
