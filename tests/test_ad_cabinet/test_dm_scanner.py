"""Тесты dm_scanner (Этап 1 — единый роутер входящих ЛС).

После багфикса persist'ится КАЖДОЕ входящее ЛС (а не только реклама): реклама →
``route='ad_cabinet'``, не реклама → ``route='notifications'``. UPSERT обновляет
снимок и переоткрывает диалог при новом входящем.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.dialects import postgresql

from database.models import AdRequest
from modules.ad_cabinet import dm_scanner

_REGION = {
    "region_id": 7,
    "region_name": "Малмыж",
    "region_code": "mi",
    "vk_group_id": -100,
}


def _dialog(text="размещу рекламу", author_is_group=False):
    return {
        "vk_post_id": None,
        "community_vk_id": -100,
        "author_vk_id": -200 if author_is_group else 42,
        "signer_id": None,
        "peer_id": 42,
        "author_is_group": author_is_group,
        "author_name": "Иван",
        "text": text,
        "attachments": [],
        "photo_urls": [],
        "last_message_id": 999,
    }


async def _classify_ad(post, ctx=None):
    return True, 5, ["test"]


async def _classify_not(post, ctx=None):
    return False, 0, []


class _FakeInsert:
    """Минимальный дублёр ``pg_insert(...)`` для проверки собранных values/set_.

    ``excluded.last_message_id`` — реальная колонка, чтобы выражение
    ``where=excluded.last_message_id > AdRequest.last_message_id`` собиралось как
    валидный SQLAlchemy-клоз (иначе ``|`` с MagicMock падает).
    """

    def __init__(self):
        self.excluded = SimpleNamespace(
            last_message_id=AdRequest.last_message_id,
            text_snapshot=None,
            attachments_json=None,
            photo_urls_json=None,
            author_name=None,
            score=None,
            reasons_json=None,
        )
        self.values_kw: dict = {}
        self.conflict_kw: dict = {}

    def values(self, **kw):
        self.values_kw.update(kw)
        return self

    def on_conflict_do_update(self, **kw):
        self.conflict_kw.update(kw)
        return self


async def test_inserts_new_dm_ad():
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog()]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_ad
    )
    assert stats["scanned"] == 1
    assert stats["ads"] == 1
    assert stats["new"] == 1
    assert stats["new_ads"] == 1  # реклама → учитывается в Telegram-алерте
    session.commit.assert_awaited()


async def test_persists_non_ad_dm_to_notifications():
    """Не реклама теперь НЕ теряется — persist'ится и попадает в уведомления."""
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog(text="привет, как дела")]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_not
    )
    assert stats["ads"] == 0
    assert stats["new"] == 1  # строка вставлена, хоть и не реклама
    assert stats["new_ads"] == 0  # не реклама → в Telegram-алерт не идёт
    session.execute.assert_awaited()


async def test_dedup_unchanged_dm_no_reopen():
    """Повторный скан того же сообщения (не новее) → UPSERT no-op (rowcount 0)."""
    checker = MagicMock()
    checker.fetch_inbound_dialogs.return_value = [_dialog()]
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=0))

    stats = await dm_scanner.scan_region_dialogs(
        session, checker, _REGION, classify_fn=_classify_ad
    )
    assert stats["ads"] == 1
    assert stats["new"] == 0


async def test_insert_ad_routes_to_cabinet(monkeypatch):
    """Реклама → route='ad_cabinet'; can_message=True для не-группового автора."""
    fake = _FakeInsert()
    monkeypatch.setattr(dm_scanner, "pg_insert", lambda model: fake)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    await dm_scanner._insert_dm_if_new(session, _REGION, _dialog(), True, 5, ["x"])
    assert fake.values_kw["origin"] == "inbound_dm"
    assert fake.values_kw["vk_post_id"] is None
    assert fake.values_kw["last_message_id"] == 999
    assert fake.values_kw["route"] == "ad_cabinet"
    assert fake.values_kw["handling_status"] == "new"
    assert fake.values_kw["can_message"] is True
    assert fake.values_kw["can_message_checked_at"] is not None
    # UPSERT переоткрывает диалог при новом входящем (handling_status → new).
    assert fake.conflict_kw["set_"]["handling_status"] == "new"
    assert fake.conflict_kw["set_"]["handled_at"] is None


async def test_insert_non_ad_routes_to_notifications(monkeypatch):
    """Не реклама → route='notifications'."""
    fake = _FakeInsert()
    monkeypatch.setattr(dm_scanner, "pg_insert", lambda model: fake)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    await dm_scanner._insert_dm_if_new(session, _REGION, _dialog(), False, 0, [])
    assert fake.values_kw["route"] == "notifications"


async def test_insert_group_author_no_can_message(monkeypatch):
    """Автор-группа → can_message не предзаполняется (None)."""
    fake = _FakeInsert()
    monkeypatch.setattr(dm_scanner, "pg_insert", lambda model: fake)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=MagicMock(rowcount=1))

    await dm_scanner._insert_dm_if_new(
        session, _REGION, _dialog(author_is_group=True), True, 5, ["x"]
    )
    assert fake.values_kw["author_is_group"] is True
    assert fake.values_kw["can_message"] is None
    assert fake.values_kw["can_message_checked_at"] is None


# ----------------------------------------------------- ON CONFLICT predicate (регресс)


def test_upsert_on_conflict_predicate_renders_as_literal():
    """Регресс прод-2026-06-07: предикат частичного индекса в ON CONFLICT должен
    рендериться ЛИТЕРАЛОМ ``origin = 'inbound_dm'``, а не bind-параметром.

    Раньше ``index_where=AdRequest.origin == "inbound_dm"`` давало ``origin = $N``,
    и Postgres не находил частичный индекс ``uq_ad_requests_inbound_dm`` →
    InvalidColumnReferenceError → КАЖДОЕ входящее ЛС терялось. Компилируем БЕЗ
    literal_binds (иначе все binds стали бы литералами и тест бы не различал) —
    text()-предикат всё равно остаётся литералом, bind-param остался бы ``%(...)s``.
    """
    stmt = dm_scanner._build_dm_upsert_stmt(
        _REGION, _dialog(), True, 5, ["x"], datetime(2026, 6, 7, 9, 0, 0)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT" in sql
    assert "WHERE origin = 'inbound_dm'" in sql  # литерал, не $N / %(origin)s
