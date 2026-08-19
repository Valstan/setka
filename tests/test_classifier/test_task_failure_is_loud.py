"""Таска ИИ-фильтра обязана рапортовать отказ отказом (инцидент 2026-08-19).

Трое суток ``classify_pending_posts`` возвращала ``{'status': 'ok', 'posts':
200, 'recorded': 0}``: ключ DeepSeek не доехал до воркера, все 26 чанков падали
с ``no_api_key``, а таска считала это успехом. Мониторинг успеха не видит
отказа, который рапортует успехом — это пул #145 в чистом виде.
"""

from __future__ import annotations

from tasks.celery_app import _dominant_failure, check_classifier_heartbeat


def test_dominant_failure_names_the_breakage():
    assert _dominant_failure(["no_api_key"] * 26) == "no_api_key"


def test_dominant_failure_picks_the_majority():
    assert _dominant_failure(["network", "no_api_key", "no_api_key"]) == "no_api_key"


def test_dominant_failure_empty_is_empty():
    assert _dominant_failure([]) == ""


def test_dominant_failure_survives_none_entries():
    assert _dominant_failure([None, None, "network"]) == "unknown"


def test_zero_verdicts_returns_error_status(monkeypatch):
    """Полный путь таски на форме инцидента: 200 постов, 0 вердиктов, no_api_key."""
    import tasks.celery_app as ca

    monkeypatch.setattr("config.classifier.classifier_disabled", lambda: False, raising=False)
    monkeypatch.setattr("config.classifier.headless_enabled", lambda: True, raising=False)
    monkeypatch.setattr("config.classifier.get_pending_max", lambda: 200, raising=False)
    monkeypatch.setattr("config.classifier.get_region_allowlist", lambda: [], raising=False)
    monkeypatch.setattr("config.classifier.get_source_days", lambda: 3, raising=False)
    monkeypatch.setattr("config.classifier.get_headless_chunk_size", lambda: 10, raising=False)
    monkeypatch.setattr("modules.secrets_bootstrap.ensure_secret", lambda name: False)

    posts = [{"lip": f"L{i}", "text": "x", "region_code": "mi"} for i in range(200)]

    async def _fake_pending(session, **kwargs):
        return posts

    async def _fake_postulates(session):
        return "постулаты"

    monkeypatch.setattr("modules.classifier.service.fetch_pending", _fake_pending)
    monkeypatch.setattr("modules.classifier.rules.render_effective_postulates", _fake_postulates)
    monkeypatch.setattr(
        "modules.classifier.headless.classify_posts",
        lambda posts, **kw: {
            "verdicts": [],
            "failures": ["no_api_key"] * 26,
            "problems": [],
            "tokens": 0,
        },
    )
    monkeypatch.setattr("modules.classifier.headless.summarize", lambda run: "summary")

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def commit(self):
            return None

    monkeypatch.setattr("database.connection.AsyncSessionLocal", lambda: _Session())

    out = ca.classify_pending_posts()
    assert out["status"] == "error:no_api_key"
    assert out["posts"] == 200
    assert out["recorded"] == 0


def test_heartbeat_task_is_registered():
    """Сторож должен быть зарегистрирован — иначе beat будет слать в пустоту
    (жёсткий гейт tests/test_celery_task_names.py ловит это же с другой стороны)."""
    assert check_classifier_heartbeat.name == "tasks.celery_app.check_classifier_heartbeat"
