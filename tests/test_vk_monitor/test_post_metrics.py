"""Общий батч-фетчер метрик постов (wall.getById).

Единственное место, где парсится ответ ВК по метрикам. Тесты держат два
свойства: NULL не превращается в 0, и падение одного батча не уносит остальные.
"""

from datetime import datetime

from modules.vk_monitor.post_metrics import fetch_metrics_for_token, parse_metrics_items


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
    fetch_metrics_for_token(FakeApi, refs, batch_size=100)
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
    out = fetch_metrics_for_token(FlakyApi, refs, batch_size=100)
    assert (-1, 200) in out, "второй батч обязан отработать после падения первого"


def test_response_may_be_dict_with_items():
    class DictApi:
        class wall:
            @staticmethod
            def getById(posts):
                return {"items": [{"owner_id": -5, "id": 1, "likes": {"count": 2}}]}

    out = fetch_metrics_for_token(DictApi, [(-5, 1)])
    assert out[(-5, 1)]["likes"] == 2
