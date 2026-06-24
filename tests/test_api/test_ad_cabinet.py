"""Тесты API рекламного кабинета (web/api/ad_cabinet).

Сессия БД мокается (AsyncMock), VK-маршрутизация/отправка monkeypatch'атся —
без реальной БД и VK, в стиле tests/test_api/test_communities_changed_category.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

import modules.notifications.vk_actions as vk_actions
import modules.vk_token_router as token_router
from database.models import AdRequest, MessageTemplate
from web.api import ad_cabinet as api


def _scalars_all(objs):
    r = MagicMock()
    r.scalars.return_value.all.return_value = objs
    return r


def _ad_request(**kw):
    defaults = dict(
        id=1,
        region_id=7,
        community_vk_id=-100,
        community_name="Малмыж Инфо",
        vk_post_id=5,
        author_vk_id=42,
        signer_id=None,
        peer_id=42,
        author_name="Иван",
        author_is_group=False,
        status="new",
        prepared_message="Здравствуйте, Иван!",
    )
    defaults.update(kw)
    return AdRequest(**defaults)


# ---------------------------------------------------------------- list


async def test_list_requests_serializes():
    ar = _ad_request()
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([ar]))
    out = await api.list_requests(db=db)
    assert len(out["requests"]) == 1
    assert out["requests"][0]["id"] == 1
    assert out["requests"][0]["vk_post_url"] == "https://vk.com/wall-100_5"


# ---------------------------------------------------------------- prepare


async def test_prepare_renders_and_saves():
    ar = _ad_request(author_name="Пётр", prepared_message=None)
    tpl = MessageTemplate(
        id=3,
        title="Оффер",
        body="Здравствуйте, {author_name}! Пишу из «{community_name}».",
        category="ad_offer",
    )
    db = AsyncMock()
    db.get = AsyncMock(side_effect=[ar, tpl])
    out = await api.prepare_reply(1, api.PrepareIn(template_id=3), db=db)
    assert "Пётр" in out["prepared_message"]
    assert "Малмыж Инфо" in out["prepared_message"]
    assert ar.prepared_message == out["prepared_message"]
    assert ar.template_id == 3


async def test_prepare_404_unknown_request():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.prepare_reply(999, api.PrepareIn(template_id=3), db=db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- send


async def test_send_not_allowed_returns_deeplink(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=False))
    monkeypatch.setattr(vk_actions, "send_message", MagicMock())

    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["personal_deeplink"] == "https://vk.com/im?sel=42"
    assert ar.status == "new"  # статус не меняем — модератор подтвердит вручную
    vk_actions.send_message.assert_not_called()


async def test_send_allowed_sends_and_marks_contacted(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=True))
    monkeypatch.setattr(
        vk_actions,
        "send_message",
        MagicMock(return_value={"success": True, "message_id": 555, "via": "community-token"}),
    )

    out = await api.send_reply(1, db=db)
    assert out["success"] is True
    assert out["vk_message_id"] == 555
    assert ar.status == "contacted"
    assert ar.vk_message_id == 555
    assert ar.contacted_at is not None


async def test_send_901_fallback(monkeypatch):
    ar = _ad_request()
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    # precheck неизвестен → пробуем отправить, VK вернёт 901.
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=None))
    monkeypatch.setattr(
        vk_actions,
        "send_message",
        MagicMock(
            return_value={
                "success": False,
                "error_code": 901,
                "allowed": False,
                "personal_deeplink": "https://vk.com/im?sel=42",
            }
        ),
    )

    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["personal_deeplink"] == "https://vk.com/im?sel=42"
    assert ar.status == "new"


async def test_send_author_group_blocked():
    ar = _ad_request(author_is_group=True, peer_id=-200)
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["reason"] == "author_is_group"


async def test_send_requires_prepared_message():
    ar = _ad_request(prepared_message=None)
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    with pytest.raises(HTTPException) as exc:
        await api.send_reply(1, db=db)
    assert exc.value.status_code == 400


# ------------------------------------------------ send: reuse fresh precheck


async def test_send_reuses_fresh_precheck_skips_vk(monkeypatch):
    """Свежий can_message со скана → messages_allowed не дёргаем повторно."""
    from datetime import datetime

    ar = _ad_request(can_message=True, can_message_checked_at=datetime.utcnow())
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    ma = MagicMock(return_value=True)
    monkeypatch.setattr(vk_actions, "messages_allowed", ma)
    monkeypatch.setattr(
        vk_actions,
        "send_message",
        MagicMock(return_value={"success": True, "message_id": 9, "via": "community-token"}),
    )
    out = await api.send_reply(1, db=db)
    assert out["success"] is True
    ma.assert_not_called()  # использован кэш precheck


async def test_send_reuses_fresh_precheck_denied(monkeypatch):
    """Свежий can_message=False → сразу deeplink, без VK-вызова и отправки."""
    from datetime import datetime

    ar = _ad_request(can_message=False, can_message_checked_at=datetime.utcnow())
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    ma = MagicMock(return_value=True)
    monkeypatch.setattr(vk_actions, "messages_allowed", ma)
    monkeypatch.setattr(vk_actions, "send_message", MagicMock())
    out = await api.send_reply(1, db=db)
    assert out["allowed"] is False
    assert out["personal_deeplink"] == "https://vk.com/im?sel=42"
    ma.assert_not_called()
    vk_actions.send_message.assert_not_called()


# ---------------------------------------------------------------- bulk action


async def test_bulk_status_updates_many():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=2))
    out = await api.bulk_action(
        api.BulkActionIn(ids=[1, 2], action="status", status="skipped"), db=db
    )
    assert out["action"] == "status"
    assert out["status"] == "skipped"
    assert out["affected"] == 2
    db.commit.assert_awaited()


async def test_bulk_status_contacted_stamps_time():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=3))
    out = await api.bulk_action(
        api.BulkActionIn(ids=[1, 2, 3], action="status", status="contacted"), db=db
    )
    assert out["affected"] == 3
    # status update + contacted_at update = два execute.
    assert db.execute.await_count == 2


async def test_bulk_delete():
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(rowcount=5))
    out = await api.bulk_action(api.BulkActionIn(ids=[1, 2, 3, 4, 5], action="delete"), db=db)
    assert out["action"] == "delete"
    assert out["affected"] == 5


async def test_bulk_invalid_action():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.bulk_action(api.BulkActionIn(ids=[1], action="frobnicate"), db=db)
    assert exc.value.status_code == 400


async def test_bulk_invalid_status():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.bulk_action(api.BulkActionIn(ids=[1], action="status", status="bogus"), db=db)
    assert exc.value.status_code == 400


async def test_bulk_empty_ids():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.bulk_action(api.BulkActionIn(ids=[], action="delete"), db=db)
    assert exc.value.status_code == 400


# ---------------------------------------------------------------- status


async def test_set_status_invalid():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.set_status(1, api.StatusIn(status="bogus"), db=db)
    assert exc.value.status_code == 400


async def test_set_status_published():
    ar = _ad_request()
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    out = await api.set_status(1, api.StatusIn(status="published"), db=db)
    assert out["status"] == "published"
    assert ar.status == "published"


# ---------------------------------------------------------------- send with body/images


async def test_send_uses_edited_message_and_selected_images(monkeypatch):
    """Правки оператора и выбранные картинки доходят до send_message."""
    ar = _ad_request(prepared_message="старый текст")
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=True))
    captured = {}

    def _fake_send(**kw):
        captured.update(kw)
        return {"success": True, "message_id": 7, "via": "community-token"}

    monkeypatch.setattr(vk_actions, "send_message", MagicMock(side_effect=_fake_send))
    monkeypatch.setattr(
        api, "_build_offer_attachment", lambda *a, **k: "photo-1_2" if k.get("filenames") else ""
    )

    out = await api.send_reply(1, api.SendIn(message="новый текст", images=["a.png"]), db=db)
    assert out["success"] is True
    assert ar.prepared_message == "новый текст"
    assert captured["message"] == "новый текст"
    assert captured["attachment"] == "photo-1_2"


async def test_send_empty_images_attaches_nothing(monkeypatch, tmp_path):
    """Пустой список картинок = текст без вложений (выбор пуст, не legacy)."""
    ar = _ad_request(prepared_message="привет")
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(api, "_offer_dir", lambda: tmp_path)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    monkeypatch.setattr(vk_actions, "messages_allowed", MagicMock(return_value=True))
    captured = {}

    def _fake_send(**kw):
        captured.update(kw)
        return {"success": True, "message_id": 8, "via": "user-token"}

    monkeypatch.setattr(vk_actions, "send_message", MagicMock(side_effect=_fake_send))
    # При images=[] фильтр оставит paths пустым → '' (никаких картинок).
    out = await api.send_reply(1, api.SendIn(message="привет", images=[]), db=db)
    assert out["success"] is True
    assert captured.get("attachment") is None  # 'attachment or None'


# ---------------------------------------------------------------- offer images library


class _FakeUpload:
    """Минимальный stand-in для fastapi.UploadFile (нужны filename + read())."""

    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


async def test_offer_images_upload_list_delete(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_offer_dir", lambda: tmp_path)

    up = await api.upload_offer_image(file=_FakeUpload("Прайс 1.png", b"\x89PNG..."))
    assert up["name"] == "Прайс 1.png"
    assert up["url"].startswith("/static/ad_offers/")
    assert (tmp_path / "Прайс 1.png").is_file()

    listed = await api.list_offer_images()
    assert [i["name"] for i in listed["images"]] == ["Прайс 1.png"]

    res = await api.delete_offer_image("Прайс 1.png")
    assert res["success"] is True
    assert not (tmp_path / "Прайс 1.png").exists()


async def test_offer_images_reject_bad_ext(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_offer_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc:
        await api.upload_offer_image(file=_FakeUpload("virus.exe", b"x"))
    assert exc.value.status_code == 400


async def test_offer_images_reject_oversize(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_offer_dir", lambda: tmp_path)
    big = b"x" * (api._MAX_IMG_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        await api.upload_offer_image(file=_FakeUpload("big.png", big))
    assert exc.value.status_code == 400


def test_safe_offer_name_blocks_traversal():
    # Пустое/скрытое/«..» — отклоняем.
    for bad in ("", "   ", "..", ".hidden"):
        with pytest.raises(HTTPException):
            api._safe_offer_name(bad)
    # Traversal нейтрализуется извлечением базового имени (не уходим из каталога).
    assert api._safe_offer_name("../secret.png") == "secret.png"
    assert api._safe_offer_name("dir/sub/ok.png") == "ok.png"
    assert api._safe_offer_name("/etc/passwd.png") == "passwd.png"


async def test_delete_offer_image_404(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_offer_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc:
        await api.delete_offer_image("nope.png")
    assert exc.value.status_code == 404


# ---------------------------------------------------------------- thread (block A)


async def test_thread_non_dm_returns_reason():
    """Для предложки переписки нет → пустой список + reason='not_dm'."""
    ar = _ad_request(origin="suggested")
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    out = await api.get_thread(1, db=db)
    assert out == {"messages": [], "reason": "not_dm"}


async def test_thread_returns_messages(monkeypatch):
    """ЛС-заявка → история из VKDialogsChecker.fetch_history."""
    import modules.notifications.vk_dialogs_checker as dc

    ar = _ad_request(origin="inbound_dm", vk_post_id=None, peer_id=42)
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=("utok", {})))
    fake_checker = MagicMock()
    fake_checker.fetch_history.return_value = [
        {
            "out": False,
            "from_id": 42,
            "from_name": "Иван",
            "text": "hi",
            "date": 100,
            "attachments": 0,
        }
    ]
    monkeypatch.setattr(dc, "VKDialogsChecker", MagicMock(return_value=fake_checker))

    out = await api.get_thread(1, db=db)
    assert len(out["messages"]) == 1
    assert out["messages"][0]["text"] == "hi"
    fake_checker.fetch_history.assert_called_once()


async def test_thread_no_token(monkeypatch):
    ar = _ad_request(origin="inbound_dm", vk_post_id=None, peer_id=42)
    db = AsyncMock()
    db.add = MagicMock()  # реальная AsyncSession.add синхронна (лог взаимодействий)
    db.get = AsyncMock(return_value=ar)
    monkeypatch.setattr(token_router, "load_vk_routing", AsyncMock(return_value=(None, {})))
    out = await api.get_thread(1, db=db)
    assert out == {"messages": [], "reason": "no_token"}


async def test_thread_404_unknown():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.get_thread(999, db=db)
    assert exc.value.status_code == 404


async def test_list_requests_origin_filter():
    """origin прокидывается в WHERE (smoke: вызов не падает, сериализует)."""
    ar = _ad_request(origin="inbound_dm", vk_post_id=None)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([ar]))
    out = await api.list_requests(origin="inbound_dm", db=db)
    assert len(out["requests"]) == 1
    assert out["requests"][0]["origin"] == "inbound_dm"


async def test_list_requests_route_filter():
    """route прокидывается в WHERE (инбокс кабинета берёт route='ad_cabinet')."""
    ar = _ad_request(origin="inbound_dm", vk_post_id=None, route="ad_cabinet")
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([ar]))
    out = await api.list_requests(route="ad_cabinet", db=db)
    assert out["requests"][0]["route"] == "ad_cabinet"


async def test_list_requests_orders_by_score_desc():
    """Триаж: ORDER BY score desc, затем detected_at desc (brain GO 2026-06-23).

    БД мокается, поэтому проверяем САМ построенный statement: компилируем SQL и
    убеждаемся, что score сортируется первым и по убыванию (actionable наверх),
    detected_at — вторичный ключ (свежие среди равного score).
    """
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_scalars_all([]))
    await api.list_requests(db=db)
    stmt = db.execute.call_args.args[0]
    sql = str(stmt.compile(compile_kwargs={"literal_binds": False})).lower()
    order_clause = sql.split("order by", 1)[1]
    assert "ad_requests.score desc" in order_clause
    # score стоит раньше detected_at в ключе сортировки.
    assert order_clause.index("ad_requests.score desc") < order_clause.index(
        "ad_requests.detected_at desc"
    )


# ------------------------------------------------ route / handling (Этап 1)


async def test_set_route_moves_to_notifications_and_resets_handling():
    """«Не реклама → в уведомления»: route меняется, handling сбрасывается в new."""
    ar = _ad_request(origin="inbound_dm", vk_post_id=None, route="ad_cabinet")
    ar.handling_status = "done"
    from datetime import datetime

    ar.handled_at = datetime.utcnow()
    db = AsyncMock()
    db.add = MagicMock()  # log_interaction → session.add (синхронно)
    db.get = AsyncMock(return_value=ar)
    out = await api.set_route(1, api.RouteIn(route="notifications"), db=db)
    assert out["route"] == "notifications"
    assert ar.route == "notifications"
    assert ar.handling_status == "new"
    assert ar.handled_at is None
    db.commit.assert_awaited()


async def test_set_route_invalid():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.set_route(1, api.RouteIn(route="bogus"), db=db)
    assert exc.value.status_code == 400


async def test_set_route_404():
    db = AsyncMock()
    db.get = AsyncMock(return_value=None)
    with pytest.raises(HTTPException) as exc:
        await api.set_route(999, api.RouteIn(route="notifications"), db=db)
    assert exc.value.status_code == 404


async def test_set_handling_done_stamps_handled_at():
    ar = _ad_request(origin="inbound_dm", vk_post_id=None, route="notifications")
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    out = await api.set_handling(1, api.HandlingIn(handling_status="done"), db=db)
    assert out["handling_status"] == "done"
    assert ar.handling_status == "done"
    assert ar.handled_at is not None


async def test_set_handling_reopen_clears_handled_at():
    from datetime import datetime

    ar = _ad_request(origin="inbound_dm", vk_post_id=None, route="notifications")
    ar.handling_status = "done"
    ar.handled_at = datetime.utcnow()
    db = AsyncMock()
    db.get = AsyncMock(return_value=ar)
    await api.set_handling(1, api.HandlingIn(handling_status="in_progress"), db=db)
    assert ar.handling_status == "in_progress"
    assert ar.handled_at is None


async def test_set_handling_invalid():
    db = AsyncMock()
    with pytest.raises(HTTPException) as exc:
        await api.set_handling(1, api.HandlingIn(handling_status="bogus"), db=db)
    assert exc.value.status_code == 400
