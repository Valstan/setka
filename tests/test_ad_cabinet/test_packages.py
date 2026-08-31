"""Тесты пакетов постов кабинета (заказ владельца 2026-08-26).

Правила: доступный пакет → заказ ТОЛЬКО в счёт пакета (цены 0), сверх остатка —
отказ; месячный исчерпанный → блок до конца месяца; postpaid-долг → блок всего;
prepaid до галочки «оплачено» недоступен; бессрочный исчерпанный → общий прайс;
отмена/отказ/сбой VK возвращают пост в пакет. Анти-спам: один пост клиента в
одно сообщество в один календарный день МСК.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from database.models import AdClientPackage
from modules.ad_cabinet import client_orders, packages

from .test_client_orders import (
    MSK_NOW,
    FakePublisher,
    _factory,
    _msk_to_unix,
    _no_attachments,
    _seed_client,
    _seed_regions,
)

TODAY = MSK_NOW.date()


def _pkg(
    client_id,
    *,
    kind="free_promo",
    total=3,
    used=0,
    price=0,
    start=None,
    end=None,
    paid=False,
    active=True,
):
    return AdClientPackage(
        client_id=client_id,
        kind=kind,
        posts_total=total,
        posts_used=used,
        price=price,
        period_start=start,
        period_end=end,
        is_active=active,
        paid_at=datetime(2026, 8, 26) if paid else None,
    )


async def _submit(session, client, *, region_ids, when=None, publisher=None):
    return await client_orders.submit_order(
        session,
        client=client,
        user_id=1,
        text="реклама",
        image_paths=[],
        region_ids=region_ids,
        publish_at=when or (MSK_NOW + timedelta(days=2)),
        publish_now=False,
        publisher_factory=_factory(publisher or FakePublisher()),
        attachment_builder=_no_attachments,
        msk_to_unix=_msk_to_unix,
        now=MSK_NOW,
    )


# ─── get_state ───────────────────────────────────────────────────


@pytest.mark.asyncio
class TestGetState:
    async def test_no_packages(self, db_session):
        c = await _seed_client(db_session)
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["block_reason"] is None and s["package"] is None

    async def test_free_promo_available_immediately(self, db_session):
        c = await _seed_client(db_session)
        db_session.add(_pkg(c.id))
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["package"] is not None and s["package"].kind == "free_promo"

    async def test_prepaid_waits_for_paid_mark(self, db_session):
        """«Оплатил 5 постов, я ставлю галочку — разблокируется»."""
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, kind="prepaid", total=5, price=1500, paid=False)
        db_session.add(pkg)
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["package"] is None and s["block_reason"] is None
        pkg.paid_at = datetime(2026, 8, 26)
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["package"] is not None and s["package"].kind == "prepaid"

    async def test_postpaid_debt_blocks_everything(self, db_session):
        """Месяц кончился, оплаты нет → создание постов запрещено."""
        c = await _seed_client(db_session)
        db_session.add(
            _pkg(
                c.id,
                kind="postpaid",
                total=10,
                price=3000,
                start=date(2026, 8, 1),
                end=date(2026, 8, 31),
                paid=False,
            )
        )
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=date(2026, 9, 1))
        assert s["block_reason"] is not None and "не оплачен" in s["block_reason"]

    async def test_postpaid_paid_period_over_no_block(self, db_session):
        """Оплаченный истёкший месяц долгом не является — общий прайс."""
        c = await _seed_client(db_session)
        db_session.add(
            _pkg(
                c.id,
                kind="postpaid",
                total=10,
                price=3000,
                start=date(2026, 8, 1),
                end=date(2026, 8, 31),
                paid=True,
            )
        )
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=date(2026, 9, 1))
        assert s["block_reason"] is None and s["package"] is None

    async def test_monthly_exhausted_blocks_until_period_end(self, db_session):
        """«Больше он не сможет выпустить — на тот месяц, на который куплен»."""
        c = await _seed_client(db_session)
        db_session.add(
            _pkg(
                c.id,
                kind="postpaid",
                total=10,
                used=10,
                price=3000,
                start=date(2026, 9, 1),
                end=date(2026, 9, 30),
            )
        )
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["block_reason"] is not None and "исчерпан" in s["block_reason"]

    async def test_termless_exhausted_falls_back_to_price(self, db_session):
        """Бессрочный пакет закончился → клиент снова на общем прайсе."""
        c = await _seed_client(db_session)
        db_session.add(_pkg(c.id, kind="prepaid", total=5, used=5, paid=True))
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["block_reason"] is None and s["package"] is None

    async def test_promo_spent_before_paid_package(self, db_session):
        c = await _seed_client(db_session)
        db_session.add(_pkg(c.id, kind="prepaid", total=5, paid=True))
        db_session.add(_pkg(c.id, kind="free_promo", total=3))
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=TODAY)
        assert s["package"].kind == "free_promo"


# ─── submit_order с пакетом ──────────────────────────────────────


@pytest.mark.asyncio
class TestOrderWithPackage:
    async def test_package_order_is_free_and_counted(self, db_session):
        ids = await _seed_regions(db_session, 2)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(db_session, c, region_ids=ids)
        assert res["price_total"] == 0
        assert res["package"]["id"] == pkg.id
        assert all(p.package_id == pkg.id and float(p.price) == 0 for p in res["posts"])
        assert pkg.posts_used == 2

    async def test_order_beyond_remaining_refused(self, db_session):
        ids = await _seed_regions(db_session, 3)
        c = await _seed_client(db_session)
        db_session.add(_pkg(c.id, total=3, used=2))
        await db_session.flush()
        with pytest.raises(client_orders.OrderError) as e:
            await _submit(db_session, c, region_ids=ids)
        assert "осталось 1" in str(e.value)

    async def test_blocked_client_cannot_order(self, db_session):
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        db_session.add(
            _pkg(
                c.id,
                kind="postpaid",
                total=10,
                price=3000,
                start=date(2026, 8, 1),
                end=date(2026, 8, 31),
                paid=False,
            )
        )
        await db_session.flush()
        with pytest.raises(client_orders.OrderError):
            await _submit(db_session, c, region_ids=ids)

    async def test_no_package_uses_price(self, db_session):
        ids = await _seed_regions(db_session, 2)
        c = await _seed_client(db_session)
        res = await _submit(db_session, c, region_ids=ids)
        assert res["price_total"] > 0 and res["package"] is None

    async def test_vk_failure_refunds_package(self, db_session):
        """Trusted-клиент, VK не принял одну группу — пост вернулся в пакет."""
        ids = await _seed_regions(db_session, 2)
        c = await _seed_client(db_session, trusted=True)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        publisher = FakePublisher(fail_groups={-100})
        res = await _submit(db_session, c, region_ids=ids, publisher=publisher)
        statuses = sorted(p.status for p in res["posts"])
        assert statuses == ["failed", "scheduled"]
        assert pkg.posts_used == 1  # 2 списали, 1 вернули за failed


# ─── возвраты ────────────────────────────────────────────────────


@pytest.mark.asyncio
class TestRefunds:
    async def test_reject_refunds(self, db_session):
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(db_session, c, region_ids=ids)
        assert pkg.posts_used == 1
        await client_orders.reject_post(db_session, res["posts"][0], comment="нет")
        assert pkg.posts_used == 0

    async def test_approve_failure_refunds(self, db_session):
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(db_session, c, region_ids=ids)
        post = res["posts"][0]
        await client_orders.approve_post(
            db_session,
            post,
            publisher_factory=_factory(FakePublisher(fail_groups={-100})),
            attachment_builder=_no_attachments,
            msk_to_unix=_msk_to_unix,
            now=MSK_NOW,
        )
        assert post.status == "failed"
        assert pkg.posts_used == 0

    async def test_refund_never_goes_negative(self, db_session):
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3, used=0)
        db_session.add(pkg)
        await db_session.flush()
        from database.models import AdScheduledPost

        post = AdScheduledPost(
            community_vk_id=-100,
            text="x",
            publish_date=MSK_NOW,
            status="cancelled",
            client_id=c.id,
            package_id=pkg.id,
        )
        db_session.add(post)
        await db_session.flush()
        await packages.refund_post(db_session, post)
        assert pkg.posts_used == 0


# ─── анти-спам: один пост в сообщество в день ────────────────────


@pytest.mark.asyncio
class TestDailySlot:
    async def test_second_post_same_day_same_community_refused(self, db_session):
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        await _submit(db_session, c, region_ids=ids)
        with pytest.raises(client_orders.OrderError) as e:
            await _submit(db_session, c, region_ids=ids)
        assert "не больше одного" in str(e.value)

    async def test_same_day_other_communities_ok(self, db_session):
        """«В один день пять постов — но в пять разных сообществ»."""
        ids = await _seed_regions(db_session, 3)
        c = await _seed_client(db_session)
        await _submit(db_session, c, region_ids=ids[:1])
        res = await _submit(db_session, c, region_ids=ids[1:])
        assert len(res["posts"]) == 2

    async def test_next_day_same_community_ok(self, db_session):
        """Календарный день, не скользящие сутки: вечер + следующее утро — можно."""
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        await _submit(db_session, c, region_ids=ids, when=MSK_NOW + timedelta(days=2))
        res = await _submit(db_session, c, region_ids=ids, when=MSK_NOW + timedelta(days=3))
        assert len(res["posts"]) == 1

    async def test_cancelled_frees_the_slot(self, db_session):
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        res = await _submit(db_session, c, region_ids=ids)
        res["posts"][0].status = "cancelled"
        await db_session.flush()
        res2 = await _submit(db_session, c, region_ids=ids)
        assert len(res2["posts"]) == 1


# ─── фиксы adversarial-ревью 2026-08-26 ──────────────────────────


@pytest.mark.asyncio
class TestReviewFixes:
    async def test_extend_in_debt_unblocks(self, db_session):
        """Блокер: «продлить в долг» ОБЯЗАН снимать блок — без mark_paid."""
        from web.api.ad_crm import package_extend

        c = await _seed_client(db_session)
        old = _pkg(
            c.id,
            kind="postpaid",
            total=10,
            price=3000,
            start=date(2026, 8, 1),
            end=date(2026, 8, 31),
            paid=False,
        )
        db_session.add(old)
        await db_session.flush()
        s = await packages.get_state(db_session, c.id, today=date(2026, 9, 2))
        assert s["block_reason"] is not None  # долг блокирует
        await package_extend(old.id, db=db_session)
        s = await packages.get_state(db_session, c.id, today=date(2026, 9, 2))
        assert s["block_reason"] is None
        assert s["package"] is not None and s["package"].period_start == date(2026, 9, 1)

    async def test_client_cancel_refunds_package(self, db_session):
        """Блокер-тест: строка refund в cancel_post обязана уметь краснеть."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from web.api.advertiser_cabinet import cancel_post

        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(db_session, c, region_ids=ids)
        post = res["posts"][0]
        await db_session.commit()
        req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)), query_params={})
        with patch("modules.ad_cabinet.advertiser_link.resolve_client", return_value=c):
            out = await cancel_post(post.id, req, db_session)
        assert out["status"] == "cancelled"
        await db_session.refresh(pkg)
        assert pkg.posts_used == 0

    async def test_cancel_failed_post_does_not_double_refund(self, db_session):
        """Блокер: failed уже возвращён — cancel не чеканит второй слот."""
        from types import SimpleNamespace
        from unittest.mock import patch

        from web.api.advertiser_cabinet import cancel_post

        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session, trusted=True)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(
            db_session, c, region_ids=ids, publisher=FakePublisher(fail_groups={-100})
        )
        post = res["posts"][0]
        assert post.status == "failed"
        await db_session.commit()
        await db_session.refresh(pkg)
        assert pkg.posts_used == 0  # уже возвращён при сбое
        req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)), query_params={})
        with patch("modules.ad_cabinet.advertiser_link.resolve_client", return_value=c):
            out = await cancel_post(post.id, req, db_session)
        assert out["status"] == "failed"  # терминален, отменять нечего
        await db_session.refresh(pkg)
        assert pkg.posts_used == 0  # второго возврата нет

    async def test_refund_is_idempotent_by_construction(self, db_session):
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3, used=2)
        db_session.add(pkg)
        await db_session.flush()
        from database.models import AdScheduledPost

        post = AdScheduledPost(
            community_vk_id=-100,
            text="x",
            publish_date=MSK_NOW,
            status="rejected",
            client_id=c.id,
            package_id=pkg.id,
        )
        db_session.add(post)
        await db_session.flush()
        await packages.refund_post(db_session, post)
        await packages.refund_post(db_session, post)  # второй вызов — no-op
        await db_session.refresh(pkg)
        assert pkg.posts_used == 1
        assert post.package_id is None

    async def test_operator_cancel_refunds(self, db_session):
        """Should-fix: операторская отмена тоже возвращает пост в пакет."""
        from web.api.ad_cabinet import cancel_scheduled

        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=3)
        db_session.add(pkg)
        await db_session.flush()
        res = await _submit(db_session, c, region_ids=ids)
        post = res["posts"][0]  # pending, vk_postponed_post_id нет → VK не зовётся
        await db_session.commit()
        out = await cancel_scheduled(post.id, db=db_session)
        assert out["status"] == "cancelled"
        await db_session.refresh(pkg)
        assert pkg.posts_used == 0

    async def test_monthly_package_rejects_publish_outside_period(self, db_session):
        """Should-fix: квота месяца не переносится отложкой в другой месяц."""
        ids = await _seed_regions(db_session, 1)
        c = await _seed_client(db_session)
        db_session.add(
            _pkg(c.id, kind="postpaid", total=10, start=date(2026, 9, 1), end=date(2026, 9, 30))
        )
        await db_session.flush()
        with pytest.raises(client_orders.OrderError) as e:
            await _submit(db_session, c, region_ids=ids, when=datetime(2026, 10, 15, 12, 0))
        assert "внутри периода" in str(e.value)

    async def test_extend_twice_conflicts(self, db_session):
        from fastapi import HTTPException

        from web.api.ad_crm import PackageIn, create_package, package_extend

        c = await _seed_client(db_session)
        pkg = await create_package(
            c.id,
            PackageIn(kind="postpaid", posts_total=10, price=3000, monthly=True),
            db=db_session,
        )
        await package_extend(pkg["id"], db=db_session)
        with pytest.raises(HTTPException) as e:
            await package_extend(pkg["id"], db=db_session)
        assert e.value.status_code == 409

    async def test_quote_reflects_package_and_block(self, db_session):
        from types import SimpleNamespace
        from unittest.mock import patch

        from web.api.advertiser_cabinet import QuoteIn, quote

        ids = await _seed_regions(db_session, 2)
        c = await _seed_client(db_session)
        pkg = _pkg(c.id, total=1)
        db_session.add(pkg)
        await db_session.flush()
        req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)), query_params={})
        with patch("modules.ad_cabinet.advertiser_link.resolve_client", return_value=c):
            q = await quote(QuoteIn(region_ids=ids), req, db_session)
        assert q["price"] == 0 and q["over_limit"] is True
        assert q["package"]["posts_left"] == 1

    async def test_routes_registered(self):
        """Пины разводки: опечатка в декораторе не должна уезжать зелёной."""
        import main

        paths = {getattr(r, "path", None) for r in main.app.routes}
        for p in (
            "/api/ad-crm/clients/{client_id}/packages",
            "/api/ad-crm/packages/{package_id}/mark-paid",
            "/api/ad-crm/packages/{package_id}/extend",
            "/api/ad-crm/packages/{package_id}/deactivate",
            "/api/ad-crm/packages/{package_id}/site-ad-done",
        ):
            assert p in paths, p

    async def test_summary_exposes_package_and_block(self, db_session):
        from types import SimpleNamespace
        from unittest.mock import patch

        from web.api.advertiser_cabinet import summary

        c = await _seed_client(db_session)
        db_session.add(_pkg(c.id, total=3))
        await db_session.flush()
        req = SimpleNamespace(state=SimpleNamespace(user=SimpleNamespace(id=1)), query_params={})
        with patch("modules.ad_cabinet.advertiser_link.resolve_client", return_value=c):
            s = await summary(req, db_session)
        assert s["package"]["kind"] == "free_promo" and s["package_block"] is None


# ─── владельческий API ───────────────────────────────────────────


@pytest.mark.asyncio
class TestOwnerPackagesApi:
    async def test_create_monthly_and_promo(self, db_session):
        from web.api.ad_crm import PackageIn, create_package

        c = await _seed_client(db_session)
        promo = await create_package(
            c.id, PackageIn(kind="free_promo", posts_total=3, site_ad=True), db=db_session
        )
        assert promo["paid"] is True and promo["price"] == 0 and promo["site_ad"] is True
        monthly = await create_package(
            c.id,
            PackageIn(kind="postpaid", posts_total=10, price=3000, monthly=True),
            db=db_session,
        )
        assert monthly["period_start"].endswith("-01") and monthly["period_end"] is not None

    async def test_mark_paid_and_extend(self, db_session):
        from web.api.ad_crm import PackageIn, create_package, package_extend, package_mark_paid

        c = await _seed_client(db_session)
        pkg = await create_package(
            c.id,
            PackageIn(kind="postpaid", posts_total=10, price=3000, monthly=True),
            db=db_session,
        )
        paid = await package_mark_paid(pkg["id"], db=db_session)
        assert paid["paid"] is True
        ext = await package_extend(pkg["id"], db=db_session)
        assert ext["period_start"] > pkg["period_end"]
        assert ext["paid"] is False and ext["posts_used"] == 0

    async def test_extend_termless_rejected(self, db_session):
        from fastapi import HTTPException

        from web.api.ad_crm import PackageIn, create_package, package_extend

        c = await _seed_client(db_session)
        pkg = await create_package(
            c.id, PackageIn(kind="prepaid", posts_total=5, price=1500, paid=True), db=db_session
        )
        with pytest.raises(HTTPException):
            await package_extend(pkg["id"], db=db_session)
