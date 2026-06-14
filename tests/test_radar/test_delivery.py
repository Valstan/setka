"""Tests for modules/radar/delivery — выводы кабинета (045): формат, курсор, тест."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from database.models_extended import RadarOutput
from modules.radar import delivery

# ───────────────────────────── format_item ─────────────────────────────


def test_format_excerpt_truncates_and_adds_link():
    item = {"title": "", "text": "x" * 1000, "url": "https://e.com/1", "source_title": "Src"}
    out = delivery.format_item(item, "excerpt_link", source_title="Src")
    assert "📡 Src" in out
    assert "🔗 https://e.com/1" in out
    assert "…" in out  # обрезано
    assert len(out) < 1000  # начало, не целиком


def test_format_full_keeps_whole_text():
    body = "y" * 1000
    item = {"title": "", "text": body, "url": "https://e.com/2"}
    out = delivery.format_item(item, "full")
    assert body in out
    assert "🔗 https://e.com/2" in out


def test_format_empty_item_is_empty():
    assert delivery.format_item({"text": "", "url": ""}, "excerpt_link") == ""


# ─────────────────────────── fake session ───────────────────────────


class _FakeSession:
    """Очередь результатов: outputs → items(output1) → items(output2) …"""

    def __init__(self, outputs, items_by_output):
        self._outputs = outputs
        self._items = list(items_by_output)
        self.committed = False
        self._first = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        if self._first:
            self._first = False
            result.scalars.return_value.all.return_value = self._outputs
            result.all.return_value = []
        else:
            rows = self._items.pop(0) if self._items else []
            result.all.return_value = rows
            result.scalars.return_value.all.return_value = rows
        return result

    async def commit(self):
        self.committed = True


def _output(otype="telegram", **kw):
    o = RadarOutput(
        user_id=kw.get("user_id", 1),
        type=otype,
        target=kw.get("target", "@me"),
        mode=kw.get("mode", "excerpt_link"),
        is_active=kw.get("is_active", True),
        last_item_id=kw.get("last_item_id", 0),
        fail_count=kw.get("fail_count", 0),
    )
    o.id = kw.get("id", 1)
    return o


def _item_row(item_id, text="hello", source_title="Src"):
    item = SimpleNamespace(
        to_dict=lambda iid=item_id, t=text: {
            "id": iid,
            "title": "",
            "text": t,
            "url": f"https://e.com/{iid}",
            "media": [],
        }
    )
    return (item, source_title)


# ─────────────────────────── deliver_new_items ───────────────────────────


@pytest.mark.asyncio
async def test_delivery_sends_new_items_and_advances_cursor():
    output = _output(last_item_id=5)
    fake = _FakeSession([output], [[_item_row(6), _item_row(7)]])
    sent = []

    async def tg(o, item, text):
        sent.append((o.id, item["id"], text))
        return True

    summary = await delivery.deliver_new_items(
        session_factory=lambda: fake, tg_sender=tg, vk_sender=tg, throttle=0
    )

    assert summary == {"outputs": 1, "delivered": 2, "failed": 0}
    assert [s[1] for s in sent] == [6, 7]
    assert output.last_item_id == 7  # курсор продвинут за последним
    assert output.fail_count == 0
    assert output.last_error is None
    assert fake.committed


@pytest.mark.asyncio
async def test_delivery_advances_cursor_even_on_send_failure():
    """At-most-once: битый элемент не клинит поток, курсор всё равно двигается."""
    output = _output(last_item_id=0)
    fake = _FakeSession([output], [[_item_row(1), _item_row(2)]])

    async def failing(o, item, text):
        return False

    summary = await delivery.deliver_new_items(
        session_factory=lambda: fake, tg_sender=failing, vk_sender=failing, throttle=0
    )

    assert summary["failed"] == 2
    assert summary["delivered"] == 0
    assert output.last_item_id == 2  # продвинут несмотря на сбой
    assert output.fail_count == 1
    assert output.last_error and "доставка не удалась" in output.last_error


@pytest.mark.asyncio
async def test_delivery_no_outputs_is_noop():
    fake = _FakeSession([], [])
    summary = await delivery.deliver_new_items(session_factory=lambda: fake, throttle=0)
    assert summary == {"outputs": 0, "delivered": 0, "failed": 0}
    assert not fake.committed


@pytest.mark.asyncio
async def test_delivery_skips_output_with_no_new_items():
    output = _output(last_item_id=99)
    fake = _FakeSession([output], [[]])  # нет новых элементов
    summary = await delivery.deliver_new_items(session_factory=lambda: fake, throttle=0)
    assert summary == {"outputs": 0, "delivered": 0, "failed": 0}


@pytest.mark.asyncio
async def test_delivery_routes_vk_to_vk_sender():
    output = _output(otype="vk", target="-123", last_item_id=0)
    fake = _FakeSession([output], [[_item_row(1)]])
    tg_called, vk_called = [], []

    async def tg(o, item, text):
        tg_called.append(item["id"])
        return True

    async def vk(o, item, text):
        vk_called.append(item["id"])
        return True

    await delivery.deliver_new_items(
        session_factory=lambda: fake, tg_sender=tg, vk_sender=vk, throttle=0
    )
    assert vk_called == [1]
    assert tg_called == []


# ─────────────────────────── send_test_output ───────────────────────────


class _OneOutputSession:
    def __init__(self, output):
        self._output = output
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        result = MagicMock()
        result.scalar_one_or_none.return_value = self._output
        return result

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_test_output_sends_sample_and_clears_error():
    output = _output(otype="telegram", fail_count=3)
    output.last_error = "была ошибка"
    fake = _OneOutputSession(output)
    captured = []

    async def tg(o, item, text):
        captured.append(text)
        return True

    res = await delivery.send_test_output(
        output_id=1, user_id=1, session_factory=lambda: fake, tg_sender=tg, vk_sender=tg
    )
    assert res["ok"] is True
    assert captured and "тестовое сообщение" in captured[0]
    assert output.fail_count == 0
    assert output.last_error is None


@pytest.mark.asyncio
async def test_test_output_feed_is_always_ok_without_send():
    output = _output(otype="feed", target=None)
    fake = _OneOutputSession(output)
    res = await delivery.send_test_output(output_id=1, user_id=1, session_factory=lambda: fake)
    assert res["ok"] is True


@pytest.mark.asyncio
async def test_test_output_missing_returns_not_found():
    fake = _OneOutputSession(None)
    res = await delivery.send_test_output(output_id=999, user_id=1, session_factory=lambda: fake)
    assert res["ok"] is False
    assert res["detail"] == "Вывод не найден"
