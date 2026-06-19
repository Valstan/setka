"""Тесты диспетчера сетевой рассылки: claim/publish/throttle/repeat/completion.

VK-публикация инъектируется (publish), сессия — фейковая (различает select по
имени таблицы, Insert=claim → rowcount, Update=запись результата).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from unittest.mock import MagicMock

from database.models import BroadcastCampaign, BroadcastPublication, BroadcastTarget
from modules.broadcast import dispatcher as d

_NOW = datetime(2026, 6, 14, 20, 0)


class _FakeSession:
    def __init__(self, *, campaigns=None, targets=None, existing=None, claim_rowcounts=None):
        self.campaigns = campaigns or []
        self.targets = targets or []
        self.existing = existing or []
        self.claim_rowcounts = list(claim_rowcounts) if claim_rowcounts is not None else None
        self.commits = 0
        self.updates = 0
        self.inserts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, stmt):
        cls = stmt.__class__.__name__
        res = MagicMock()
        if cls == "Insert":
            self.inserts += 1
            res.rowcount = self.claim_rowcounts.pop(0) if self.claim_rowcounts else 1
            return res
        if cls == "Update":
            self.updates += 1
            res.rowcount = 1
            return res
        text = str(stmt)
        if "broadcast_campaigns" in text:
            res.scalars.return_value.all.return_value = self.campaigns
        elif "broadcast_targets" in text:
            res.scalars.return_value.all.return_value = self.targets
        elif "broadcast_publications" in text:
            res.scalars.return_value.all.return_value = self.existing
        else:
            res.scalars.return_value.all.return_value = []
        return res

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        pass


def _camp(**kw):
    defaults = dict(
        id=1,
        body="привет",
        image_names=[],
        attachments=None,
        status="scheduled",
        scheduled_at=_NOW,
        repeat_count=1,
        repeat_interval_hours=24,
        runs_done=0,
        next_run_at=_NOW,
    )
    defaults.update(kw)
    return BroadcastCampaign(**defaults)


def _targets(*gids):
    return [
        BroadcastTarget(id=i + 1, campaign_id=1, group_id=g, name=f"g{g}")
        for i, g in enumerate(gids)
    ]


def _ok_publish(record):
    def _p(gid, text, atts):
        record.append((gid, text, atts))
        return {
            "success": True,
            "post_id": 100 + abs(gid) % 100,
            "url": f"https://vk.com/wall{gid}_1",
        }

    return _p


# ---- compute_reschedule (чистая) ----


def test_reschedule_single_run_done():
    assert d.compute_reschedule(
        runs_done=0, repeat_count=1, next_run_at=_NOW, interval_hours=24, now=_NOW
    ) == (1, "done", _NOW)


def test_reschedule_more_runs_advances():
    runs, status, nxt = d.compute_reschedule(
        runs_done=0, repeat_count=3, next_run_at=_NOW, interval_hours=24, now=_NOW
    )
    assert runs == 1 and status == "scheduled"
    assert (nxt - _NOW).total_seconds() == 24 * 3600


def test_reschedule_last_run_done():
    assert (
        d.compute_reschedule(
            runs_done=2, repeat_count=3, next_run_at=_NOW, interval_hours=24, now=_NOW
        )[1]
        == "done"
    )


# ---- dispatch_campaign ----


# ---- _parse_attachments_map (чистая) ----


def test_parse_attachments_map_json():
    raw = '{"100": "photo-100_1,photo-100_2", "200": "photo-200_9"}'
    assert d._parse_attachments_map(raw) == {
        100: "photo-100_1,photo-100_2",
        200: "photo-200_9",
    }


def test_parse_attachments_map_empty_and_legacy():
    assert d._parse_attachments_map(None) == {}
    assert d._parse_attachments_map("") == {}
    assert d._parse_attachments_map("{}") == {}
    # legacy-формат (одна строка attachment'ов, старый кэш) → игнорируем
    assert d._parse_attachments_map("photo-100_1,photo-100_2") == {}
    # пустые значения в карте отбрасываем (текст-онли для этой цели)
    assert d._parse_attachments_map('{"100": "", "200": "photo-200_1"}') == {200: "photo-200_1"}


# ---- per-target media (фикс бага: картинки шли только текстом) ----


def test_per_target_attachments_built_and_cached():
    """Картинки грузятся ПО КАЖДОЙ цели; каждая получает свою attachment-строку,
    карта кэшируется в campaign.attachments (JSON)."""
    sent = []
    camp = _camp(image_names=["a.jpg"])

    async def _build(campaign, targets):
        # имитируем per-group заливку: у каждой цели свой owner-photo
        return {abs(int(t.group_id)): f"photo{t.group_id}_1" for t in targets}

    fake = _FakeSession(targets=_targets(-100, -200), claim_rowcounts=[1, 1])
    out = asyncio.run(
        d.dispatch_campaign(
            fake, camp, publish=_ok_publish(sent), build_attachments=_build, interval=0, now=_NOW
        )
    )
    assert out["published"] == 2 and out["complete"] is True
    by_gid = {gid: atts for gid, _t, atts in sent}
    assert by_gid[-100] == ["photo-100_1"]
    assert by_gid[-200] == ["photo-200_1"]
    # кэш — JSON-карта per-group, пригодная к повторным прогонам
    assert d._parse_attachments_map(camp.attachments) == {
        100: "photo-100_1",
        200: "photo-200_1",
    }


def test_empty_build_cached_as_text_only():
    """Заливка вернула пусто (нет токена/сбой) → кэшируем '{}' и шлём текстом, не
    дёргая заливку каждый прогон."""
    sent = []
    camp = _camp(image_names=["a.jpg"])

    async def _build(campaign, targets):
        return {}

    fake = _FakeSession(targets=_targets(-100), claim_rowcounts=[1])
    asyncio.run(
        d.dispatch_campaign(
            fake, camp, publish=_ok_publish(sent), build_attachments=_build, interval=0, now=_NOW
        )
    )
    assert sent[0][2] is None  # текст-онли
    assert camp.attachments == "{}"


def test_cached_attachments_not_rebuilt():
    """attachments уже закэшированы (не None) → build_attachments не вызывается."""
    sent = []
    camp = _camp(image_names=["a.jpg"], attachments='{"100": "photo-100_7"}')
    called = {"n": 0}

    async def _build(campaign, targets):
        called["n"] += 1
        return {}

    fake = _FakeSession(targets=_targets(-100), claim_rowcounts=[1])
    asyncio.run(
        d.dispatch_campaign(
            fake, camp, publish=_ok_publish(sent), build_attachments=_build, interval=0, now=_NOW
        )
    )
    assert called["n"] == 0
    assert sent[0][2] == ["photo-100_7"]


def test_publishes_all_targets_and_completes():
    sent = []
    camp = _camp()
    fake = _FakeSession(targets=_targets(-100, -200), claim_rowcounts=[1, 1])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW)
    )
    assert out["published"] == 2 and out["complete"] is True
    assert {s[0] for s in sent} == {-100, -200}
    assert camp.runs_done == 1 and camp.status == "done"
    assert fake.updates == 2  # каждая публикация записана


def test_already_claimed_target_skipped_run_incomplete():
    sent = []
    camp = _camp()
    # Первую цель «забрал» конкурентный беат (claim=0) → публикуем только вторую,
    # прогон НЕ полный (одна цель без терминальной строки) → runs_done не растёт.
    fake = _FakeSession(targets=_targets(-100, -200), claim_rowcounts=[0, 1])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW)
    )
    assert out["published"] == 1 and out["complete"] is False
    assert [s[0] for s in sent] == [-200]
    assert camp.runs_done == 0 and camp.status == "scheduled"


def test_repeat_schedules_next_run():
    sent = []
    camp = _camp(repeat_count=2, repeat_interval_hours=24)
    fake = _FakeSession(targets=_targets(-100), claim_rowcounts=[1])
    asyncio.run(d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW))
    assert camp.runs_done == 1 and camp.status == "scheduled"
    assert (camp.next_run_at - _NOW).total_seconds() == 24 * 3600


def test_no_targets_marks_done():
    camp = _camp()
    fake = _FakeSession(targets=[])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish([]), interval=0, now=_NOW)
    )
    assert out["skipped"] == "no_targets" and camp.status == "done"


def test_publish_error_isolated_run_still_completes():
    def _p(gid, text, atts):
        if gid == -100:
            raise RuntimeError("boom")
        return {"success": True, "post_id": 5, "url": "u"}

    camp = _camp()
    fake = _FakeSession(targets=_targets(-100, -200), claim_rowcounts=[1, 1])
    out = asyncio.run(d.dispatch_campaign(fake, camp, publish=_p, interval=0, now=_NOW))
    # Ошибка в одну цель не валит вторую; обе терминальны → прогон завершён.
    assert out["published"] == 1 and out["complete"] is True
    assert camp.runs_done == 1 and camp.status == "done"


def test_existing_terminal_targets_skipped():
    # У одной цели уже есть published-строка (частичный прошлый прогон) → её не
    # переклеймливаем, добиваем вторую, прогон завершается.
    sent = []
    camp = _camp()
    existing = [MagicMock(group_id=-100, status="published")]
    fake = _FakeSession(targets=_targets(-100, -200), existing=existing, claim_rowcounts=[1])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW)
    )
    assert [s[0] for s in sent] == [-200]
    assert out["complete"] is True and camp.runs_done == 1


def test_stale_pending_reclaimed_then_run_completes():
    # Зависший pending (процесс умер mid-run) старше grace → реклеймится в error
    # (терминально), прогон завершается, кампания не виснет навечно.
    sent = []
    camp = _camp()
    old = datetime.utcnow() - timedelta(seconds=d.STALE_PENDING_SECONDS + 60)
    stuck = BroadcastPublication(
        id=7, campaign_id=1, group_id=-100, run_index=0, status="pending", published_at=old
    )
    fake = _FakeSession(targets=_targets(-100, -200), existing=[stuck], claim_rowcounts=[1])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW)
    )
    assert stuck.status == "error"  # реклеймлено
    assert [s[0] for s in sent] == [-200]  # -100 не перепубликовываем (дубль-защита)
    assert out["complete"] is True and camp.runs_done == 1


def test_fresh_pending_not_reclaimed():
    # Свежий pending (claim только что, прогон идёт) НЕ трогаем.
    sent = []
    camp = _camp()
    fresh = BroadcastPublication(
        id=8,
        campaign_id=1,
        group_id=-100,
        run_index=0,
        status="pending",
        published_at=datetime.utcnow(),
    )
    fake = _FakeSession(targets=_targets(-100), existing=[fresh])
    out = asyncio.run(
        d.dispatch_campaign(fake, camp, publish=_ok_publish(sent), interval=0, now=_NOW)
    )
    assert fresh.status == "pending"  # не реклеймлено
    assert sent == []  # -100 в done_groups (pending) → пропущен
    assert out["complete"] is False and camp.runs_done == 0  # прогон не завершён


# ---- run_broadcast_dispatch (внешний цикл) ----


def test_run_dispatch_disabled(monkeypatch):
    monkeypatch.setenv("BROADCAST_DISABLED", "1")
    out = asyncio.run(d.run_broadcast_dispatch())
    assert out == {"skipped": "disabled", "dispatched": 0}


def test_run_dispatch_processes_due(monkeypatch):
    monkeypatch.setenv("BROADCAST_DISABLED", "0")
    sent = []
    camp = _camp()
    fake = _FakeSession(campaigns=[camp], targets=_targets(-100), claim_rowcounts=[1])
    monkeypatch.setattr(d, "touch_heartbeat", lambda **k: None)
    out = asyncio.run(
        d.run_broadcast_dispatch(
            session_factory=lambda: fake,
            publish=_ok_publish(sent),
            build_attachments=None,
            interval=0,
            now=_NOW,
        )
    )
    assert out["dispatched"] == 1
    assert sent and camp.status == "done"
