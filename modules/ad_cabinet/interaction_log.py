"""Единая точка записи событий рекламного кабинета (таймлайн / audit-log).

Все мутации кабинета (ответ клиенту, смена статуса, планирование, публикация,
оплата, ручная заметка) пишут событие сюда — чтобы оператор видел хронологию с
датой-временем и не раздувать каждый эндпоинт повторяющимся кодом.

``log_interaction`` только добавляет запись в сессию (``session.add``) и НЕ
коммитит — коммит делает вызывающий эндпоинт в своей транзакции. Возвращает
созданный ``AdInteraction`` (id появится после flush/commit вызывающего).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from database.models import AdInteraction


def log_interaction(
    session,
    *,
    kind: str,
    summary: Optional[str] = None,
    client_id: Optional[int] = None,
    ad_request_id: Optional[int] = None,
    scheduled_post_id: Optional[int] = None,
    publication_id: Optional[int] = None,
    payment_id: Optional[int] = None,
    meta: Optional[Dict[str, Any]] = None,
    actor: str = "operator",
) -> AdInteraction:
    """Записать событие в журнал кабинета. Без commit — это делает вызывающий."""
    rec = AdInteraction(
        kind=kind,
        summary=summary,
        client_id=client_id,
        ad_request_id=ad_request_id,
        scheduled_post_id=scheduled_post_id,
        publication_id=publication_id,
        payment_id=payment_id,
        meta_json=meta,
        actor=actor,
    )
    session.add(rec)
    return rec
