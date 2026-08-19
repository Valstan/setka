"""Окно свежести аудита собранных постов — ОДНО на измерение и на витрину.

Окно живёт здесь, а не в двух модулях, потому что это шов между «что мы
меряем» (``metrics_refresh`` догоняет метрики через ВК) и «что показываем»
(``rating`` строит топ-N). Две копии константы и два дословно одинаковых
предиката разъезжаются молча: витрина начнёт показывать строки, метрики
которых таска уже не обновляет, и ни одна проверка об этом не скажет — цифры
на панели останутся правдоподобными, просто устаревшими.

Тот же довод, что у общего фетчера ``vk_monitor/post_metrics.py`` и общего
``vk_post_datetime``: одна реализация вместо двух копий, которые совпадают
только пока их не трогали.
"""

from __future__ import annotations

from datetime import datetime, timedelta

AUDIT_WINDOW_HOURS = 72


def window_cutoff(hours: int = AUDIT_WINDOW_HOURS, *, now: datetime | None = None) -> datetime:
    """Граница окна: всё, что старше — вне обеих сторон (и меры, и показа)."""
    return (now or datetime.utcnow()) - timedelta(hours=hours)


def in_window(cutoff: datetime):
    """SQLAlchemy-предикат «строка аудита попадает в окно ``cutoff``».

    Окно считается по ``published_at`` (возраст поста). Строки, где она ещё
    ``NULL`` — наследие до миграции 080 — добираются по ``collected_at``:
    ``published_at > cutoff`` на NULL даёт NULL (не True), и без второй ветки
    OR такие строки выпали бы из выборки молча, а на проде их 7774 штуки.
    """
    from sqlalchemy import or_

    from database.models_extended import CollectedPostAudit

    return or_(
        CollectedPostAudit.published_at > cutoff,
        (CollectedPostAudit.published_at.is_(None)) & (CollectedPostAudit.collected_at > cutoff),
    )
