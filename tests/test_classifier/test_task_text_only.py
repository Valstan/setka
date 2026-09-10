"""Фоновая таска headless обязана брать батч только из постов с текстом.

Замер прода 2026-09-10: ``posts=200 ... skipped_no_text=177`` — движок
пропускает посты без текста, вердикта у них не появляется, и они занимают
места батча на каждом прогоне всё окно свежести. Лечится в ``fetch_pending``
(``text_only``), но только если таска этот флаг действительно передаёт.
"""

from __future__ import annotations


def test_headless_task_requests_text_only_batch(monkeypatch):
    import tasks.celery_app as ca

    monkeypatch.setattr("config.classifier.classifier_disabled", lambda: False, raising=False)
    monkeypatch.setattr("config.classifier.headless_enabled", lambda: True, raising=False)
    monkeypatch.setattr("config.classifier.get_pending_max", lambda: 200, raising=False)
    monkeypatch.setattr("config.classifier.get_region_allowlist", lambda: [], raising=False)
    monkeypatch.setattr("config.classifier.get_source_days", lambda: 1, raising=False)
    monkeypatch.setattr("modules.secrets_bootstrap.ensure_secret", lambda name: False)

    seen = {}

    async def _fake_pending(session, **kwargs):
        seen.update(kwargs)
        return []

    monkeypatch.setattr("modules.classifier.service.fetch_pending", _fake_pending)

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("database.connection.AsyncSessionLocal", lambda: _Session())

    out = ca.classify_pending_posts()
    assert out == {"status": "ok", "posts": 0}
    assert seen.get("text_only") is True
