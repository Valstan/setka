"""Тесты телеметрии кабинета (заказ владельца 2026-08-26).

Владелец должен видеть, что клиенты ПРИХОДЯТ (signup/visit), что они ДЕЛАЮТ
(заказы/отказы) и что у них ЛОМАЕТСЯ (js_error) — не дожидаясь жалобы. Здесь:
запись событий в ``ad_interactions``, дедуп шумных потоков, пинги владельцу,
сводная лента ``/api/ad-crm/cabinet-activity``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from database.models import AdClient, AdInteraction
from database.models_extended import RadarUser
from modules.ad_cabinet.owner_ping import notify_owner, ping_dedup_pass
from web.api.advertiser_cabinet import OnboardingIn, TelemetryIn, onboarding, telemetry


def _req(user):
    return SimpleNamespace(state=SimpleNamespace(user=user), query_params={})


async def _mk_user(db, login="client1", role="radar"):
    user = RadarUser(login=login, password_hash="x", role=role, is_active=True)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


async def _interactions(db, kind=None):
    stmt = select(AdInteraction)
    if kind:
        stmt = stmt.where(AdInteraction.kind == kind)
    return (await db.execute(stmt)).scalars().all()


# ----------------------------------------------------------------- owner_ping
@pytest.fixture(autouse=True)
def _reset_owner_ping_state():
    """Модуль кэширует Redis-клиент и down-стейт — тесты не должны делиться им."""
    import modules.ad_cabinet.owner_ping as op

    op._redis_client = None
    op._redis_down_until = 0.0
    op._local_marks.clear()
    op._local_counts.clear()
    yield
    op._redis_client = None
    op._redis_down_until = 0.0
    op._local_marks.clear()
    op._local_counts.clear()


class TestOwnerPing:
    def test_sends_via_first_available_bot(self):
        posted = {}

        def fake_post(url, json=None, timeout=None):
            posted["url"] = url
            posted["json"] = json
            return MagicMock()

        with (
            patch("requests.post", side_effect=fake_post),
            patch("config.runtime.TELEGRAM_TOKENS", {"VALSTANBOT": "tok123"}),
            patch("config.runtime.TELEGRAM_ALERT_CHAT_ID", "-100"),
        ):
            assert notify_owner("привет") is True
        assert "tok123" in posted["url"]
        assert posted["json"]["text"] == "привет"

    def test_not_configured_is_false_not_crash(self):
        with (
            patch("config.runtime.TELEGRAM_TOKENS", {}),
            patch("config.runtime.TELEGRAM_ALERT_CHAT_ID", None),
        ):
            assert notify_owner("привет") is False

    def test_dedup_blocks_second_ping(self):
        with (
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", side_effect=[True, False]) as dd,
            patch("requests.post") as post,
            patch("config.runtime.TELEGRAM_TOKENS", {"VALSTANBOT": "tok"}),
            patch("config.runtime.TELEGRAM_ALERT_CHAT_ID", "-100"),
        ):
            assert notify_owner("a", dedup_key="k") is True
            assert notify_owner("a", dedup_key="k") is False
        assert post.call_count == 1
        assert dd.call_count == 2

    def test_dedup_redis_set_nx(self):
        fake_redis = MagicMock()
        fake_redis.set.return_value = True
        with patch("modules.vk_monitor.rate_limiter._build_redis_client", return_value=fake_redis):
            assert ping_dedup_pass("x", ttl=60) is True
        fake_redis.set.assert_called_once_with("setka:cabinet_ping:x", "1", nx=True, ex=60)

    def test_dedup_redis_down_falls_back_to_local(self):
        """Redis лёг → in-process слой: первый проходит, повтор в окне — нет
        (should-fix ревью: аутэйдж Redis не снимает backpressure целиком)."""
        with patch(
            "modules.vk_monitor.rate_limiter._build_redis_client",
            side_effect=RuntimeError("down"),
        ):
            assert ping_dedup_pass("x", ttl=60) is True
            assert ping_dedup_pass("x", ttl=60) is False

    def test_budget_redis_down_falls_back_to_local(self):
        from modules.ad_cabinet.owner_ping import event_budget_pass

        with patch(
            "modules.vk_monitor.rate_limiter._build_redis_client",
            side_effect=RuntimeError("down"),
        ):
            assert event_budget_pass("b", limit=2, ttl=60) is True
            assert event_budget_pass("b", limit=2, ttl=60) is True
            assert event_budget_pass("b", limit=2, ttl=60) is False

    def test_failed_send_releases_dedup_key(self):
        """Сбой Telegram возвращает dedup-ключ — иначе честный пинг молчал бы
        весь ttl (nice-to-have ревью, принято)."""
        import modules.ad_cabinet.owner_ping as op

        with (
            patch.object(op, "ping_dedup_pass", side_effect=[True, True]),
            patch.object(op, "release_dedup") as release,
            patch.object(op, "_send_telegram", return_value=False),
        ):
            assert op.notify_owner("a", dedup_key="k") is False
        release.assert_called_once_with("k")

    def test_stable_digest_is_stable(self):
        """Встроенный hash() солится на рестарте — отпечаток обязан быть детерминирован."""
        from modules.ad_cabinet.owner_ping import stable_digest

        assert stable_digest("boom") == stable_digest("boom")
        assert stable_digest("boom") != stable_digest("bam")
        assert len(stable_digest("любой текст")) == 10


# ----------------------------------------------------------------- /telemetry
@pytest.mark.asyncio
class TestTelemetry:
    async def test_visit_logged_once_per_dedup_window(self, db_session):
        user = await _mk_user(db_session)
        with patch(
            "modules.ad_cabinet.owner_ping.ping_dedup_pass", side_effect=[True, False]
        ) as dd:
            r1 = await telemetry(TelemetryIn(kind="visit"), _req(user), db_session)
            r2 = await telemetry(TelemetryIn(kind="visit"), _req(user), db_session)
        assert r1 == {"ok": True} and r2 == {"ok": True}  # окна троттлов не раскрываются
        rows = await _interactions(db_session, "cabinet_visit")
        assert len(rows) == 1
        assert rows[0].actor == "client"
        assert user.login in (rows[0].summary or "")
        # Ключ дедупа несёт user.id — потеря id глушила бы визиты ВСЕХ клиентов.
        assert dd.call_args_list[0].args == (f"visit:{user.id}",)

    async def test_js_error_logged_and_pinged(self, db_session):
        user = await _mk_user(db_session)
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner") as ping,
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=True),
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", return_value=True),
        ):
            await telemetry(
                TelemetryIn(kind="js_error", message="TypeError: x is null", source="app.js:42"),
                _req(user),
                db_session,
            )
        rows = await _interactions(db_session, "cabinet_js_error")
        assert len(rows) == 1
        assert "TypeError" in rows[0].summary
        assert rows[0].meta_json["source"] == "app.js:42"
        ping.assert_called_once()
        assert ping.call_args.kwargs["dedup_key"] == f"js_error:{user.id}"

    async def test_js_error_same_text_deduped(self, db_session):
        """Зацикленный onerror: та же ошибка пишется раз в окно."""
        user = await _mk_user(db_session)
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner") as ping,
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=True),
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", side_effect=[True, False]),
        ):
            await telemetry(TelemetryIn(kind="js_error", message="loop"), _req(user), db_session)
            await telemetry(TelemetryIn(kind="js_error", message="loop"), _req(user), db_session)
        assert len(await _interactions(db_session, "cabinet_js_error")) == 1
        assert ping.call_count == 2  # пинг дедупится своим ключом внутри notify_owner

    async def test_js_error_budget_stops_rotating_text(self, db_session):
        """Блокер ревью: ротация текста не обходит потолок — бюджет
        контент-независим, исчерпан → запись не создаётся вовсе."""
        user = await _mk_user(db_session)
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner"),
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=False),
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass") as dd,
        ):
            await telemetry(
                TelemetryIn(kind="js_error", message="unique-1"), _req(user), db_session
            )
            await telemetry(
                TelemetryIn(kind="js_error", message="unique-2"), _req(user), db_session
            )
        assert await _interactions(db_session, "cabinet_js_error") == []
        dd.assert_not_called()  # до по-текстового дедупа дело не доходит

    async def test_js_error_before_onboarding_has_no_client(self, db_session):
        """Ошибка на онбординге — клиента ещё нет, но событие не теряется."""
        user = await _mk_user(db_session, login="newcomer")
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner"),
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=True),
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", return_value=True),
        ):
            await telemetry(TelemetryIn(kind="js_error", message="boom"), _req(user), db_session)
        rows = await _interactions(db_session, "cabinet_js_error")
        assert rows[0].client_id is None
        assert "newcomer" in rows[0].summary

    async def test_client_attributed_when_onboarded(self, db_session):
        """Событие онбордингового клиента несёт client_id и имя карточки."""
        user = await _mk_user(db_session)
        client = AdClient(radar_user_id=user.id, name="ООО Ирис")
        db_session.add(client)
        await db_session.commit()
        with patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", return_value=True):
            await telemetry(TelemetryIn(kind="visit"), _req(user), db_session)
        rows = await _interactions(db_session, "cabinet_visit")
        assert rows[0].client_id == client.id
        assert "Ирис" in rows[0].summary

    async def test_vk_only_user_never_renders_none(self, db_session):
        """ВК-аккаунт без login: имя не должно светиться как «None»."""
        user = RadarUser(login=None, password_hash=None, role="radar", is_active=True)
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        with patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", return_value=True):
            await telemetry(TelemetryIn(kind="visit"), _req(user), db_session)
        rows = await _interactions(db_session, "cabinet_visit")
        assert "None" not in (rows[0].summary or "")
        assert f"user#{user.id}" in rows[0].summary

    async def test_kind_is_validated(self):
        with pytest.raises(Exception):
            TelemetryIn(kind="hack")


# ------------------------------------------------------------- signup logging
@pytest.mark.asyncio
class TestSignupTelemetry:
    async def test_new_onboarding_logged_and_pinged_once(self, db_session):
        user = await _mk_user(db_session)
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner") as ping,
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=True) as budget,
        ):
            await onboarding(OnboardingIn(name="ООО Ромашка"), _req(user), db_session)
            await onboarding(OnboardingIn(name="ООО Ромашка"), _req(user), db_session)
        rows = await _interactions(db_session, "cabinet_signup")
        assert len(rows) == 1  # идемпотентный повтор не плодит события
        assert "Ромашка" in rows[0].summary
        assert ping.call_count == 1
        # Пинг под глобальным бюджетом: скриптовые регистрации не заливают TG.
        assert budget.call_args.args == ("signup_ping",)

    async def test_signup_ping_budget_exhausted_still_logs(self, db_session):
        """Бюджет пинга исчерпан → событие всё равно в таймлайне."""
        user = await _mk_user(db_session, login="quiet")
        with (
            patch("modules.ad_cabinet.owner_ping.notify_owner") as ping,
            patch("modules.ad_cabinet.owner_ping.event_budget_pass", return_value=False),
        ):
            await onboarding(OnboardingIn(name="Тихий"), _req(user), db_session)
        assert len(await _interactions(db_session, "cabinet_signup")) == 1
        ping.assert_not_called()


# ----------------------------------------------------------- refused orders
@pytest.mark.asyncio
class TestRefusedOrderTelemetry:
    async def test_order_error_lands_in_timeline(self, db_session):
        from fastapi import HTTPException

        from web.api.advertiser_cabinet import OrderIn, create_order

        user = await _mk_user(db_session)
        client = AdClient(radar_user_id=user.id, name="К", trusted=False)
        db_session.add(client)
        await db_session.commit()

        with (
            patch("modules.vk_token_router.load_vk_routing", return_value=(None, {})),
            patch("modules.ad_cabinet.owner_ping.ping_dedup_pass", side_effect=[True, False]) as dd,
        ):
            for _ in range(2):
                with pytest.raises(HTTPException) as e:
                    await create_order(
                        OrderIn(text="", photos=[], region_ids=[1]), _req(user), db_session
                    )
        assert e.value.status_code == 400
        rows = await _interactions(db_session, "cabinet_order_refused")
        assert len(rows) == 1  # зацикленный невалидный запрос не заливает ленту
        assert rows[0].client_id == client.id
        assert dd.call_args_list[0].args == (f"order_refused:{client.id}",)


# ----------------------------------------------------------------- chat ping
@pytest.mark.asyncio
class TestChatPing:
    async def test_client_message_pings_owner_with_dedup(self, db_session):
        from web.api.advertiser_cabinet import ChatIn, send_chat

        user = await _mk_user(db_session, login="chatty")
        client = AdClient(radar_user_id=user.id, name="Болтун")
        db_session.add(client)
        await db_session.commit()
        with patch("modules.ad_cabinet.owner_ping.notify_owner") as ping:
            await send_chat(ChatIn(body="привет"), _req(user), db_session)
        ping.assert_called_once()
        assert ping.call_args.kwargs["dedup_key"] == f"chat:{client.id}"


# --------------------------------------------------------- сводная лента /ad
@pytest.mark.asyncio
class TestCabinetActivity:
    async def test_feed_filters_joins_and_flags(self, db_session):
        from modules.ad_cabinet.interaction_log import log_interaction
        from web.api.ad_crm import cabinet_activity

        user = await _mk_user(db_session)
        client = AdClient(radar_user_id=user.id, name="Клиент-1")
        db_session.add(client)
        await db_session.commit()

        log_interaction(db_session, kind="cabinet_signup", client_id=client.id, summary="пришёл")
        log_interaction(db_session, kind="cabinet_js_error", client_id=client.id, summary="упало")
        log_interaction(
            db_session, kind="note", client_id=client.id, summary="операторская заметка"
        )
        log_interaction(db_session, kind="cabinet_js_error", client_id=None, summary="аноним упал")
        # Операторская отмена пишет тот же kind='cancelled', но actor='operator' —
        # в ленту КЛИЕНТСКОЙ активности не идёт (should-fix ревью).
        log_interaction(
            db_session, kind="cancelled", client_id=client.id, summary="операторская отмена"
        )
        log_interaction(
            db_session,
            kind="cancelled",
            client_id=client.id,
            summary="клиентская отмена",
            actor="client",
        )
        await db_session.commit()

        data = await cabinet_activity(limit=50, db=db_session)
        kinds = [a["kind"] for a in data["activity"]]
        assert "note" not in kinds  # операторские заметки — не лента клиентов
        assert kinds.count("cabinet_js_error") == 2
        cancelled = [a for a in data["activity"] if a["kind"] == "cancelled"]
        assert [c["summary"] for c in cancelled] == ["клиентская отмена"]
        by_kind = {(a["kind"], a["summary"]): a for a in data["activity"]}
        assert by_kind[("cabinet_signup", "пришёл")]["client_name"] == "Клиент-1"
        assert by_kind[("cabinet_signup", "пришёл")]["is_error"] is False
        assert by_kind[("cabinet_js_error", "упало")]["is_error"] is True
        assert by_kind[("cabinet_js_error", "аноним упал")]["client_name"] is None

    async def test_newest_first_and_limit_clamped(self, db_session):
        from datetime import datetime

        from modules.ad_cabinet.interaction_log import log_interaction
        from web.api.ad_crm import cabinet_activity

        old = log_interaction(db_session, kind="cabinet_signup", summary="старое")
        old.created_at = datetime(2026, 1, 1)
        log_interaction(db_session, kind="cabinet_visit", summary="свежее")
        await db_session.commit()

        data = await cabinet_activity(limit=99999, db=db_session)  # clamp не роняет
        assert [a["summary"] for a in data["activity"]] == ["свежее", "старое"]

    async def test_client_timeline_hides_visits(self, db_session):
        """Карточка клиента: визиты не вытесняют заказы из окна (should-fix)."""
        from modules.ad_cabinet.interaction_log import log_interaction
        from web.api.ad_crm import client_timeline

        user = await _mk_user(db_session, login="tlc")
        client = AdClient(radar_user_id=user.id, name="К2")
        db_session.add(client)
        await db_session.commit()
        log_interaction(db_session, kind="cabinet_visit", client_id=client.id, summary="зашёл")
        log_interaction(db_session, kind="client_order", client_id=client.id, summary="заказ")
        await db_session.commit()

        data = await client_timeline(client.id, db=db_session)
        kinds = [r["kind"] for r in data["timeline"]]
        assert "client_order" in kinds
        assert "cabinet_visit" not in kinds


# ------------------------------------------------------------------ auth gate
def test_telemetry_path_is_onboarding_exact():
    """Ошибка до онбординга — самый ценный сигнал: путь открыт любому
    аутентифицированному, не только роли advertiser."""
    from middleware.auth_gate import ADVERTISER_ONBOARDING_EXACT

    assert "/api/advertiser/telemetry" in ADVERTISER_ONBOARDING_EXACT


# ------------------------------------------------------- wiring (route + UI)
# Тесты выше зовут функции напрямую — опечатка в пути роутера/фронта прошла бы
# зелёной, а маячок молча глотал бы 404 (should-fix ревью 2026-08-26).
def test_telemetry_routes_registered():
    import main

    paths = {getattr(r, "path", None) for r in main.app.routes}
    assert "/api/advertiser/telemetry" in paths
    assert "/api/ad-crm/cabinet-activity" in paths


def test_beacon_wired_in_cabinet_page():
    """Маячок инлайном и ДО основного скрипта — чтобы поймать его падение."""
    from pathlib import Path

    html = Path("web/templates/advertiser_cabinet.html").read_text(encoding="utf-8")
    assert "/api/advertiser/telemetry" in html
    assert "unhandledrejection" in html
    assert html.index("/api/advertiser/telemetry") < html.index("advertiser_cabinet.js")


def test_activity_feed_wired_in_owner_ui():
    from pathlib import Path

    assert 'id="cabinet-activity"' in Path("web/templates/ad.html").read_text(encoding="utf-8")
    assert "/ad-crm/cabinet-activity" in Path("web/static/js/ad_crm.js").read_text(encoding="utf-8")
