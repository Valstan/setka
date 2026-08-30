"""API страницы «Темы» — доли наполнения ленты (заказ владельца 2026-08-30).

Кто сюда ходит: страница ``/themes`` в операторской зоне. Доступ закрывает
``AuthGateMiddleware`` (secure by default) — ни одна ручка здесь не публичная и в
PUBLIC-списки гейта ничего не добавляется.

Откуда данные: ``classifier_themes`` (план и описания, миграция 090),
``content_classifications`` (кандидаты), ``published_posts`` (факт, миграция 091).

**Три колонки, и средняя — самая важная.** План — то, что владелец хочет;
факт — то, что вышло; кандидаты — то, из чего вообще можно выбирать. Без третьей
страница врала бы умолчанием: потолок умеет не пустить, но не умеет создать, и
ползунок «спорт 20%» при 4% кандидатов не поднимет спорт ничем. Рычаг там другой
(число слотов в расписании и пул сообществ-источников), и страница обязана это
показывать, а не молчать.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from config.classifier import get_source_days, get_theme_quota_window_hours, theme_quota_enabled
from database import models  # noqa: F401 — конфигурация мапперов
from database.connection import get_db_session
from database.models_extended import ClassifierTheme, ContentClassification
from modules.publication_journal import fetch_published_counts

logger = logging.getLogger(__name__)

router = APIRouter()

# За сколько дней считаем «кандидатов». Неделя, а не окно квоты: суточная выборка
# по редкой теме (детсад — единицы вердиктов) прыгала бы от нуля до десятков и
# читалась бы как шум, а колонка нужна для оценки «а есть ли из чего выбирать».
CANDIDATES_DAYS = 7


class SharesPut(BaseModel):
    """Частичное обновление долей.

    Семантика ``None`` тут ОТЛИЧАЕТСЯ от ``PromoSettings``, и это сознательно.
    Там ``None`` значит «не трогать», потому что снять настройку нельзя — она
    всегда имеет значение. Здесь «нет потолка» — валидное состояние, и способ его
    выразить нужен: ``null`` = снять потолок, а «не трогать» = не передавать ключ.
    """

    shares: Dict[str, Optional[float]]

    @field_validator("shares")
    @classmethod
    def _check_range(cls, value: Dict[str, Optional[float]]) -> Dict[str, Optional[float]]:
        for theme, share in value.items():
            if share is None:
                continue
            if not (0 <= float(share) <= 100):
                raise ValueError(f"доля темы «{theme}» вне диапазона 0..100: {share}")
        return value


def candidate_counts_stmt(*, days: int):
    """Запрос «сколько постов каждой темы нейросеть предложила публиковать».

    Извлечение темы из JSON вынесено в ПОДЗАПРОС, а группировка идёт по его
    колонке. Прямая группировка по `verdict['theme']` выглядит короче и работает
    на SQLite — но Postgres её отвергает: SQLAlchemy подставляет РАЗНЫЕ bind-
    параметры под один литерал `'theme'` в SELECT и в GROUP BY, а Postgres
    сравнивает выражения синтаксически и не признаёт их одним. Ошибка вылезает
    только на проде («column verdict must appear in the GROUP BY clause»), и
    тесты на SQLite о ней молчат — поэтому запрос собирается отдельной функцией,
    а сторож в тестах компилирует её ИМЕННО под диалект Postgres.

    Читаем сырой вердикт, без наложения правок оператора: правки живут в другой
    таблице и на этой колонке сказались бы единицами из тысяч, а запрос стал бы
    вдвое дороже. Колонка отвечает на вопрос «есть ли из чего выбирать», и для
    него такой точности достаточно.
    """
    cutoff = datetime.utcnow() - timedelta(days=days)
    inner = (
        select(ContentClassification.verdict["theme"].as_string().label("theme"))
        .where(
            ContentClassification.created_at >= cutoff,
            ContentClassification.verdict["action"].as_string() == "publish",
        )
        .subquery()
    )
    return select(inner.c.theme, func.count()).group_by(inner.c.theme)


async def _candidate_counts(db: AsyncSession, *, days: int) -> Dict[str, int]:
    rows = (await db.execute(candidate_counts_stmt(days=days))).all()
    return {str(theme): int(count) for theme, count in rows if theme}


def _pct(part: int, whole: int) -> Optional[float]:
    """Доля в процентах; ``None`` при пустом знаменателе — не ноль.

    Ноль сказал бы «темы не было», пустой знаменатель — «мерить ещё не по чему».
    Для страницы, которая сутки после релиза копит журнал, разница существенная.
    """
    if not whole:
        return None
    return round(part / whole * 100, 1)


@router.get("")
@router.get("/")
async def get_themes(db: AsyncSession = Depends(get_db_session)) -> Dict[str, Any]:
    """Темы с планом, фактом и кандидатами."""
    window_hours = get_theme_quota_window_hours()
    rows = (
        (await db.execute(select(ClassifierTheme).order_by(ClassifierTheme.position)))
        .scalars()
        .all()
    )
    candidates = await _candidate_counts(db, days=CANDIDATES_DAYS)
    published = await fetch_published_counts(db, window_hours=window_hours)

    candidates_total = sum(candidates.values())
    published_total = sum(published.values())

    themes = []
    planned_sum = 0.0
    for row in rows:
        share = None if row.share_percent is None else float(row.share_percent)
        if share is not None and not row.is_service:
            planned_sum += share
        cand_count = candidates.get(row.name, 0)
        cand_pct = _pct(cand_count, candidates_total)
        themes.append(
            {
                "theme": row.name,
                "position": row.position,
                "description": row.description,
                "is_service": bool(row.is_service),
                "share_percent": share,
                "candidates_count": cand_count,
                "candidates_pct": cand_pct,
                "published_count": published.get(row.name, 0),
                "published_pct": _pct(published.get(row.name, 0), published_total),
                # Цель недостижима потолком: источников по теме меньше, чем план.
                # Не ошибка — подсказка, что рычаг здесь другой.
                "unreachable": bool(
                    share is not None and cand_pct is not None and share > cand_pct
                ),
            }
        )

    return {
        "themes": themes,
        "planned_sum": round(planned_sum, 1),
        "window_hours": window_hours,
        "candidates_days": CANDIDATES_DAYS,
        "published_total": published_total,
        "candidates_total": candidates_total,
        # Реальное состояние гейта, а не «включено» по умолчанию: вся врезка квоты
        # живёт под CLASSIFIER_SELECTION_ENABLED, и её молчаливое выключение иначе
        # выглядело бы на странице точно так же, как работающие потолки.
        "quota_enabled": theme_quota_enabled(),
        "source_days": get_source_days(),
    }


@router.put("")
@router.put("/")
async def put_shares(payload: SharesPut, db: AsyncSession = Depends(get_db_session)):
    """Обновить доли. Ключа нет — не трогаем; ``null`` — снять потолок."""
    if not payload.shares:
        return {"updated": {}}

    rows = (
        (
            await db.execute(
                select(ClassifierTheme).where(ClassifierTheme.name.in_(list(payload.shares)))
            )
        )
        .scalars()
        .all()
    )
    by_name = {row.name: row for row in rows}

    missing = sorted(set(payload.shares) - set(by_name))
    if missing:
        # 404, а не тихое создание темы-призрака: доля, повисшая на несуществующей
        # теме, не применится никогда и найдётся только глазами.
        raise HTTPException(status_code=404, detail=f"нет таких тем: {', '.join(missing)}")

    service = sorted(name for name, row in by_name.items() if row.is_service)
    if service:
        raise HTTPException(
            status_code=400,
            detail=(
                f"служебным темам доля не назначается: {', '.join(service)}. "
                "«Мусор» не публикуется вовсе, «соседи» идут отдельным каналом"
            ),
        )

    updated: Dict[str, Optional[float]] = {}
    for name, share in payload.shares.items():
        by_name[name].share_percent = share
        updated[name] = share
    await db.commit()
    logger.info("theme shares updated: %s", updated)
    return {"updated": updated}


@router.post("/normalize")
async def normalize_shares(db: AsyncSession = Depends(get_db_session)):
    """Привести заданные доли к сумме 100, сохранив пропорции.

    Кнопка, а не инвариант сохранения: движок к сумме равнодушен (каждая доля —
    независимый потолок), а молча переписывать введённые владельцем числа хуже,
    чем показать, что сумма не сошлась. Темы без потолка не трогаем — «не
    ограничивать» это не ноль процентов.
    """
    rows = (
        (await db.execute(select(ClassifierTheme).order_by(ClassifierTheme.position)))
        .scalars()
        .all()
    )
    capped = [r for r in rows if r.share_percent is not None and not r.is_service]
    total = sum(float(r.share_percent) for r in capped)
    if not capped or total <= 0:
        raise HTTPException(
            status_code=400,
            detail="нечего нормализовать: ни одной темы с заданной долей больше нуля",
        )

    updated: Dict[str, float] = {}
    for row in capped:
        value = round(float(row.share_percent) / total * 100, 1)
        row.share_percent = value
        updated[row.name] = value
    await db.commit()
    return {"updated": updated, "planned_sum": round(sum(updated.values()), 1)}
