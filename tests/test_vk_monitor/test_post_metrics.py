"""Общий батч-фетчер метрик постов (wall.getById).

Единственное место, где парсится ответ ВК по метрикам. Тесты держат два
свойства: NULL не превращается в 0, и падение одного батча не уносит остальные.
"""

from datetime import datetime

import pytest

from modules.vk_monitor.post_metrics import fetch_metrics_for_token, parse_metrics_items


@pytest.fixture(autouse=True)
def _no_real_throttle(monkeypatch):
    """Тормоз подменяется счётчиком: реальный лимитер спит 0.4 с на вызов.

    Подмена — по модулю post_metrics, поэтому сам факт вызова остаётся
    проверяемым (см. test_every_batch_goes_through_the_shared_throttle).
    """
    calls = []
    monkeypatch.setattr(
        "modules.vk_monitor.post_metrics.enforce_token_rate_limit",
        lambda token, method="": calls.append((token, method)),
    )
    return calls


def test_parse_reads_all_four_metrics_and_date():
    items = [
        {
            "owner_id": -100,
            "id": 7,
            "date": 1787136000,
            "views": {"count": 1224},
            "likes": {"count": 7},
            "comments": {"count": 2},
            "reposts": {"count": 1},
        }
    ]
    out = parse_metrics_items(items)
    assert out[(-100, 7)] == {
        "views": 1224,
        "likes": 7,
        "comments": 2,
        "reposts": 1,
        "published_at": datetime(2026, 8, 19, 10, 40),
    }


def test_missing_views_stays_none_not_zero():
    # 10% постов приезжают без поля views. Ноль тут соврал бы: рейтинг делит
    # на (views+1), и «ноль просмотров» подняло бы пост наверх.
    items = [{"owner_id": -1, "id": 2, "likes": {"count": 3}}]
    out = parse_metrics_items(items)
    assert out[(-1, 2)]["views"] is None
    assert out[(-1, 2)]["likes"] == 3
    assert out[(-1, 2)]["comments"] is None


def test_parse_skips_items_without_usable_ids():
    items = [{"likes": {"count": 1}}, {"owner_id": "х", "id": 1}]
    assert parse_metrics_items(items) == {}


def test_fetch_splits_into_batches_of_hundred():
    calls = []

    class FakeApi:
        class wall:
            @staticmethod
            def getById(posts):
                calls.append(posts.split(","))
                return []

    refs = [(-1, i) for i in range(250)]
    fetch_metrics_for_token(FakeApi, refs, token="tok", batch_size=100)
    assert [len(c) for c in calls] == [100, 100, 50]


def test_one_failing_batch_does_not_lose_the_others():
    class FlakyApi:
        class wall:
            seen = 0

            @classmethod
            def getById(cls, posts):
                cls.seen += 1
                if cls.seen == 1:
                    raise RuntimeError("VK упал")
                return [{"owner_id": -1, "id": 200, "likes": {"count": 5}}]

    refs = [(-1, i) for i in range(150)]
    out = fetch_metrics_for_token(FlakyApi, refs, token="tok", batch_size=100)
    assert (-1, 200) in out, "второй батч обязан отработать после падения первого"


def test_response_may_be_dict_with_items():
    class DictApi:
        class wall:
            @staticmethod
            def getById(posts):
                return {"items": [{"owner_id": -5, "id": 1, "likes": {"count": 2}}]}

    out = fetch_metrics_for_token(DictApi, [(-5, 1)], token="tok")
    assert out[(-5, 1)]["likes"] == 2


def test_every_batch_goes_through_the_shared_throttle(_no_real_throttle):
    """Каждый батч тормозится общим per-token лимитером, а не уходит залпом.

    Это не гигиена: круг обновления метрик берёт тот же живой READ-токен, что
    и боевой парсер. 78 вызовов подряд — burst сильно выше 3 req/sec, за
    который ВК ставит на токен cooldown или captcha, и вспомогательная таска
    посадила бы сбор постов. Плюс тот же вызов считается в отчёте по токенам.
    """

    class FakeApi:
        class wall:
            @staticmethod
            def getById(posts):
                return []

    fetch_metrics_for_token(FakeApi, [(-1, i) for i in range(250)], token="tok-A", batch_size=100)

    assert _no_real_throttle == [("tok-A", "wall.getById")] * 3


def test_throttle_happens_even_when_the_batch_fails(_no_real_throttle):
    """Отказ батча не отменяет тормоз: следующий батч всё равно ждёт свой слот.

    Иначе на серии отказов (а именно там ВК и злится) вызовы посыпались бы
    вплотную — ровно в тот момент, когда токен и так под подозрением.
    """

    class DeadApi:
        class wall:
            @staticmethod
            def getById(posts):
                raise RuntimeError("VK упал")

    out = fetch_metrics_for_token(DeadApi, [(-1, i) for i in range(150)], token="t", batch_size=100)

    assert out == {}
    assert len(_no_real_throttle) == 2
