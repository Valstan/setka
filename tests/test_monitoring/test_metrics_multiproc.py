"""Multiprocess-mode гарантии для monitoring.metrics.

Worker-процесс Celery инкрементит дайджест-метрики в своём процессе, а
``/metrics`` живёт в web. Без ``PROMETHEUS_MULTIPROC_DIR`` + ``MultiProcessCollector``
counter'ы умирают в worker'е. Тесты ниже фиксируют две вещи:

1. ``digest_last_published_timestamp`` объявлен с ``multiprocess_mode='max'`` —
   при агрегации из нескольких процессов выбирается самое свежее значение
   (timestamp монотонно растёт).
2. В multiproc-режиме ``get_metrics`` действительно идёт через
   ``MultiProcessCollector`` и видит counter, инкрементированный в этом же
   subprocess.
"""

import subprocess
import sys
import textwrap


def test_is_multiproc_reads_env(monkeypatch):
    from monitoring import metrics

    monkeypatch.delenv("PROMETHEUS_MULTIPROC_DIR", raising=False)
    assert metrics._is_multiproc() is False

    monkeypatch.setenv("PROMETHEUS_MULTIPROC_DIR", "/tmp/some/path")
    assert metrics._is_multiproc() is True


def test_digest_timestamp_gauge_uses_max_mode():
    from monitoring.metrics import digest_last_published_timestamp

    assert digest_last_published_timestamp._multiprocess_mode == "max"


def test_in_progress_gauges_have_livesum_mode():
    from monitoring.metrics import (
        api_requests_in_progress,
        communities_monitored,
        db_connections_active,
        notifications_zero_streak,
        regions_active,
    )

    for gauge in (
        api_requests_in_progress,
        db_connections_active,
        communities_monitored,
        regions_active,
        notifications_zero_streak,
    ):
        assert (
            gauge._multiprocess_mode == "livesum"
        ), f"{gauge._name} must use multiprocess_mode='livesum'"


def test_get_metrics_in_multiproc_mode_aggregates_via_collector(tmp_path):
    """End-to-end в subprocess с выставленной ``PROMETHEUS_MULTIPROC_DIR``.

    Subprocess нужен потому что prometheus_client читает env-var в момент
    импорта metric-объектов; mmap-файлы создаются при первом ``.inc()``. Если
    выставить env-var задним числом в основном тестовом процессе, существующие
    counter'ы уже привязаны к in-memory backend.
    """
    code = textwrap.dedent(
        f"""
        import os, asyncio
        os.environ["PROMETHEUS_MULTIPROC_DIR"] = r"{tmp_path}"

        from monitoring.metrics import get_metrics, track_digest_published, _is_multiproc
        assert _is_multiproc(), "env-var must be visible to module"

        track_digest_published("smoke", "novost", "success")
        content, _ = asyncio.run(get_metrics())
        text = content.decode("utf-8")
        assert 'setka_digest_published_total' in text, text[:500]
        assert 'region="smoke"' in text, text[:500]
        assert 'setka_digest_last_published_timestamp' in text, text[:500]
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert (
        result.returncode == 0
    ), f"subprocess failed:\nstdout={result.stdout}\nstderr={result.stderr}"
    assert "OK" in result.stdout
