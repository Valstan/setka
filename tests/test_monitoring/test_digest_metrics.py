"""Тесты на ``track_digest_published`` — метрики дайджестов per region/topic."""

import time

import pytest

from monitoring.metrics import (
    digest_last_published_timestamp,
    digest_published_total,
    track_digest_published,
)


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
    assert key not in digest_last_published_timestamp._metrics, (
        "Gauge не должен апдейтиться на failed-публикации"
    )


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
