"""Пакетное чтение донорских стен через VK ``execute`` (заказ владельца 2026-08-20).

**Что мерили перед тем, как строить.** Весь расход трёх user-токенов — это
``wall.get`` по донорским стенам: 5282 сканирования групп против 5361 вызова за
те же часы, при 1520 активных донорах в 44 районах. Community-токены заменить
их не могут — проба на живом ``COMM_166980909`` дала ``ERROR 27: method is
unavailable with group auth`` на ``wall.get`` даже по собственной стене.

Отсюда единственный доступный рычаг: ``execute``, до 25 вызовов API на стороне
ВК за один запрос. Тесты стерегут две вещи — что вызовов действительно стало
меньше и что отказ пакета не оставляет район без сводки.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pytest

from modules.vk_monitor.advanced_parser import AdvancedVKParser


def _post(owner_id: int, pid: int, text: str = "текст поста") -> Dict[str, Any]:
    return {
        "id": pid,
        "owner_id": owner_id,
        "text": text,
        "date": 2_000_000_000,
        "likes": {"count": 1},
        "comments": {"count": 0},
        "reposts": {"count": 0},
        "views": {"count": 10},
    }


class FakeVK:
    """Клиент, считающий обращения обоих видов."""

    def __init__(self, *, batch_raises: bool = False, missing: List[int] = None):
        self.single_calls: List[int] = []
        self.batch_calls: List[List[int]] = []
        self.batch_raises = batch_raises
        self.missing = set(missing or [])

    def get_wall_posts_batch(self, owner_ids, count=20):
        self.batch_calls.append(list(owner_ids))
        if self.batch_raises:
            raise RuntimeError("execute отвалился")
        return {
            oid: ([] if oid in self.missing else [_post(oid, 1), _post(oid, 2)])
            for oid in owner_ids
        }

    def get_wall_posts(self, owner_id, count=20, offset=0):
        self.single_calls.append(owner_id)
        return [_post(owner_id, 1), _post(owner_id, 2)]


@pytest.mark.asyncio
async def test_batch_replaces_per_community_reads(monkeypatch):
    """Главное обещание: один пакет вместо вызова на группу."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "1")
    vk = FakeVK()
    parser = AdvancedVKParser(vk)

    await parser._prefetch_walls([101, 102, 103], 20)
    for cid in (101, 102, 103):
        posts = await parser._fetch_community_posts(cid, 20)
        assert len(posts) == 2

    assert vk.batch_calls == [[-101, -102, -103]]
    assert vk.single_calls == [], "поштучных чтений быть не должно"


@pytest.mark.asyncio
async def test_batch_failure_falls_back_to_single_reads(monkeypatch, caplog):
    """Отказ пакета не оставляет район без сводки — читаем поштучно, и громко."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "1")
    vk = FakeVK(batch_raises=True)
    parser = AdvancedVKParser(vk)

    with caplog.at_level("WARNING", logger="modules.vk_monitor.advanced_parser"):
        await parser._prefetch_walls([201, 202], 20)
    for cid in (201, 202):
        assert len(await parser._fetch_community_posts(cid, 20)) == 2

    assert vk.single_calls == [-201, -202]
    assert any("поштучно" in r.message for r in caplog.records), "тихий откат недопустим"


@pytest.mark.asyncio
async def test_gate_off_keeps_old_behaviour(monkeypatch):
    """Выключенный гейт — ровно прежний код, ни одного пакетного запроса."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "0")
    vk = FakeVK()
    parser = AdvancedVKParser(vk)

    await parser._prefetch_walls([301, 302], 20)
    for cid in (301, 302):
        await parser._fetch_community_posts(cid, 20)

    assert vk.batch_calls == []
    assert vk.single_calls == [-301, -302]


@pytest.mark.asyncio
async def test_group_absent_from_batch_is_read_singly(monkeypatch):
    """Группа, которой пакет не вернул ключа вовсе, дочитывается поштучно —
    иначе её посты молча выпали бы из сводки."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "1")

    class PartialVK(FakeVK):
        def get_wall_posts_batch(self, owner_ids, count=20):
            self.batch_calls.append(list(owner_ids))
            return {-401: [_post(-401, 1)]}  # -402 отсутствует как ключ

    vk = PartialVK()
    parser = AdvancedVKParser(vk)
    await parser._prefetch_walls([401, 402], 20)

    assert len(await parser._fetch_community_posts(401, 20)) == 1
    assert vk.single_calls == []
    assert len(await parser._fetch_community_posts(402, 20)) == 2
    assert vk.single_calls == [-402]


@pytest.mark.asyncio
async def test_empty_wall_is_not_reread(monkeypatch):
    """Пустая стена — это ответ, а не пропуск. Перечитывать её поштучно значило
    бы вернуть ровно тот расход, ради снижения которого всё делалось."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "1")
    vk = FakeVK(missing=[-501])
    parser = AdvancedVKParser(vk)
    await parser._prefetch_walls([501, 502], 20)

    assert await parser._fetch_community_posts(501, 20) == []
    assert vk.single_calls == [], "пустая стена не повод идти в ВК второй раз"


@pytest.mark.asyncio
async def test_posts_keep_their_own_owner_id(monkeypatch):
    """Сопоставление по индексу ответа перепутало бы районы местами: посты
    обязаны нести owner_id своей группы."""
    monkeypatch.setenv("VK_EXECUTE_BATCH_ENABLED", "1")
    vk = FakeVK()
    parser = AdvancedVKParser(vk)
    await parser._prefetch_walls([601, 602], 20)

    for cid in (601, 602):
        for post in await parser._fetch_community_posts(cid, 20):
            assert post["owner_id"] == -cid
