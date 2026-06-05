"""Тесты хелпера ``modules.ad_cabinet.interaction_log.log_interaction``."""

from __future__ import annotations

from unittest.mock import MagicMock

from database.models import AdInteraction
from modules.ad_cabinet.interaction_log import log_interaction


def test_log_interaction_adds_record_without_commit():
    session = MagicMock()
    rec = log_interaction(
        session,
        kind="reply_sent",
        client_id=7,
        ad_request_id=5,
        summary="Отправлен ответ",
        meta={"via": "community-token"},
    )
    assert isinstance(rec, AdInteraction)
    assert rec.kind == "reply_sent"
    assert rec.client_id == 7
    assert rec.ad_request_id == 5
    assert rec.meta_json == {"via": "community-token"}
    assert rec.actor == "operator"
    session.add.assert_called_once_with(rec)
    # Хелпер НЕ коммитит — это делает вызывающий эндпоинт.
    session.commit.assert_not_called()


def test_log_interaction_defaults():
    session = MagicMock()
    rec = log_interaction(session, kind="note")
    assert rec.kind == "note"
    assert rec.client_id is None
    assert rec.summary is None
    assert rec.actor == "operator"


def test_log_interaction_actor_override():
    session = MagicMock()
    rec = log_interaction(session, kind="published", actor="system")
    assert rec.actor == "system"
