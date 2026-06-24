"""Тесты на ``track_digest_published`` — метрики сводок per region/topic."""

import time

import pytest

from monitoring.metrics import (
    digest_last_published_timestamp,
    digest_published_total,
    publish_result_label,
    track_digest_published,
)

# --------------------------------------------------------------------------- #
# publish_result_label — регрессия 2026-06-05
#
# publish_bulletin() возвращает dict {"success": bool}, а call-sites обращались
# к .success как к атрибуту → AttributeError на каждой публикации, heartbeat
# #018 не писался, watchdog был молча мёртв.
# --------------------------------------------------------------------------- #


def test_publish_result_label_dict_success():
    assert publish_result_label({"success": True, "via": "community-token"}) == "success"


def test_publish_result_label_dict_failure():
    assert publish_result_label({"success": False, "error": "VK 27"}) == "failed"


def test_publish_result_label_dict_missing_key_is_failed():
    assert publish_result_label({"posts_published": 0}) == "failed"


def test_publish_result_label_tolerates_object_with_attr():
    """Запас на исторические пути, где возвращался объект с .success."""
    from types import SimpleNamespace

    assert publish_result_label(SimpleNamespace(success=True)) == "success"
    assert publish_result_label(SimpleNamespace(success=False)) == "failed"


def test_publish_result_label_none_is_failed():
    assert publish_result_label(None) == "failed"


@pytest.fixture(autouse=True)
def _reset_digest_metrics():
    """Сбрасываем prometheus_client регистры между тестами.

    prometheus_client держит state в process-wide singletones, поэтому
    нельзя позволить тестам влиять друг на друга через накопленный счёт.
    """
    digest_published_total.clear()
    digest_last_published_timestamp.clear()
    yield
    digest_published_total.clear()
    digest_last_published_timestamp.clear()


def test_track_digest_published_success_increments_counter():
    track_digest_published("tuzha", "novost", "success")
    sample = digest_published_total.labels(
        region="tuzha", topic="novost", result="success"
    )._value.get()
    assert sample == 1.0


def test_track_digest_published_success_updates_timestamp():
    """Для result='success' Gauge должен быть в районе текущего времени."""
    before = time.time()
    track_digest_published("mi", "novost", "success")
    after = time.time()

    ts = digest_last_published_timestamp.labels(region="mi", topic="novost")._value.get()
    assert before <= ts <= after, f"timestamp {ts} not in [{before}, {after}]"


def test_track_digest_published_failed_does_not_update_timestamp():
    """Для result='failed' Counter инкрементится, но Gauge — нет."""
    track_digest_published("tuzha", "novost", "failed")

    counter = digest_published_total.labels(
        region="tuzha", topic="novost", result="failed"
    )._value.get()
    assert counter == 1.0

    # Gauge для (tuzha, novost) не выставлен — children'а нет.
    # Доступ через ._metrics: dict labels → metric. Если ключ отсутствует — Gauge не был set.
    key = ("tuzha", "novost")
    assert (
        key not in digest_last_published_timestamp._metrics
    ), "Gauge не должен апдейтиться на failed-публикации"


def test_track_digest_published_empty_does_not_update_timestamp():
    """result='empty' — counted but no fresh timestamp."""
    track_digest_published("tuzha", "novost", "empty")
    counter = digest_published_total.labels(
        region="tuzha", topic="novost", result="empty"
    )._value.get()
    assert counter == 1.0
    assert ("tuzha", "novost") not in digest_last_published_timestamp._metrics


def test_track_digest_published_separates_regions_and_topics():
    track_digest_published("tuzha", "novost", "success")
    track_digest_published("tuzha", "mourning", "success")
    track_digest_published("mi", "novost", "success")

    for region, topic in [("tuzha", "novost"), ("tuzha", "mourning"), ("mi", "novost")]:
        c = digest_published_total.labels(region=region, topic=topic, result="success")._value.get()
        assert c == 1.0, f"(region={region}, topic={topic}) counter expected 1.0, got {c}"

    # Все три уникальные пары попадают в Gauge.
    assert {("tuzha", "novost"), ("tuzha", "mourning"), ("mi", "novost")} <= set(
        digest_last_published_timestamp._metrics.keys()
    )
