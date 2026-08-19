"""Операторская лента вердиктов HITL-классификатора (``/api/classifier-review``).

Доступ — только оператор (путь НЕ в ``PUBLIC_PREFIXES``, закрыт сессионной
cookie ``AuthGateMiddleware``), в отличие от ingest-интерфейса рутины
``/api/classifier`` (тот со своей X-API-Key защитой). Аналогично паре
gateway / gateway-stats.

Оператор смотрит пост + вердикт нейронки и реагирует: ✅ согласен / изменить
тему / поменять действие / поправить склейку. Несогласия → лог коррекций
(сырьё agree-rate + дистилляции файла-корректировщика).
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel

from database.connection import AsyncSessionLocal
from modules.classifier import rating, rules, service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/feed")
async def feed(
    region: str = Query("", description="код района (опционально)"),
    only_unreviewed: bool = Query(True, description="только неразобранные (не финализированные)"),
    limit: int = Query(50, ge=1, le=200),
):
    """Лента: пост + вердикт нейронки для оператора."""
    async with AsyncSessionLocal() as session:
        items = await service.review_feed(
            session,
            region_code=region.strip() or None,
            only_unreviewed=only_unreviewed,
            limit=limit,
        )
    return {"count": len(items), "items": items}


# --- Групповые действия -------------------------------------------------------
#
# ВЫШЕ параметризованных путей осознанно. Starlette матчит маршруты в порядке
# регистрации, и `/{classification_id}/agree`, объявленный раньше, съедал
# `/bulk/agree`: «bulk» уходил в int() → 422, кнопка «Согласен со всем блоком»
# молча не работала весь день 2026-08-19. Новые статические пути под этим
# префиксом добавлять сюда же — гейт в tests/test_classifier/test_review_api_routes.py.


@router.get("/rating/top")
async def rating_top(
    region: str = Query(
        ...,
        min_length=1,
        # \S — хотя бы один не-пробельный символ. Без этого `region=" "`
        # проходил min_length=1, .strip() схлопывал его в пустую строку, и
        # вместо 422 витрина тихо отдавала пустой топ (находка ревью #495).
        pattern=r"\S",
        description="код района (обязателен)",
    ),
    theme: str = Query("", description="тема (опционально)"),
    n: int = Query(10, ge=1, le=100, description="сколько строк в топе"),
):
    """Витрина топ-N по рейтингу — измерение, публикацию не трогает.

    ``region`` обязателен намеренно: рейтинги районов между собой
    несопоставимы (у больших пабликов свои порядки просмотров), и общий топ
    по сети был бы числом без смысла.
    """
    async with AsyncSessionLocal() as session:
        return await rating.top_by_rating(
            session, region_code=region.strip(), theme=theme.strip() or None, n=n
        )


class BulkAgree(BaseModel):
    ids: list[int]


class BulkCorrect(BaseModel):
    ids: list[int]
    verdict_type: str
    operator_value: Any


@router.post("/bulk/agree")
async def bulk_agree(body: BulkAgree = Body(...)):
    """✅ «Согласен со всей группой» — одно нажатие закрывает пачку карточек."""
    ids = [int(i) for i in (body.ids or [])][:2000]
    if not ids:
        return {"ok": False, "error": "empty ids"}
    async with AsyncSessionLocal() as session:
        return await service.bulk_agree(session, ids)


@router.post("/bulk/correct")
async def bulk_correct(body: BulkCorrect = Body(...)):
    """Правка вердикта сразу по группе (и закрытие её карточек)."""
    ids = [int(i) for i in (body.ids or [])][:2000]
    if not ids:
        return {"ok": False, "error": "empty ids"}
    async with AsyncSessionLocal() as session:
        return await service.bulk_correct(
            session,
            ids,
            verdict_type=body.verdict_type,
            operator_value=body.operator_value,
        )


@router.post("/{classification_id}/agree")
async def agree(classification_id: int):
    """✅ «Согласен со всем» — agree по всем типам + финализация (пост уходит из ленты)."""
    async with AsyncSessionLocal() as session:
        out = await service.agree_all(session, classification_id)
    return out


@router.post("/{classification_id}/finalize")
async def finalize(classification_id: int):
    """✔ «Готово» — завершить составной вердикт: правки сохранить, остальное = agree."""
    async with AsyncSessionLocal() as session:
        out = await service.finalize(session, classification_id)
    return out


class CorrectionIn(BaseModel):
    verdict_type: str  # theme | action | merge
    operator_value: Any = None


@router.post("/{classification_id}/correct")
async def correct(classification_id: int, body: CorrectionIn = Body(...)):
    """Поправка одного аспекта вердикта (theme|action|merge)."""
    async with AsyncSessionLocal() as session:
        out = await service.correct(
            session,
            classification_id,
            verdict_type=body.verdict_type,
            operator_value=body.operator_value,
        )
    return out


@router.get("/stats")
async def stats():
    """agree-rate по типам вердикта — метрика shadow-гейта (ADR-0003 §F)."""
    async with AsyncSessionLocal() as session:
        return await service.agree_rate_stats(session)


@router.get("/themes")
async def themes():
    """Темы: канонический словарь + не-канонические остатки (для пикера и редактора)."""
    async with AsyncSessionLocal() as session:
        items = await service.themes_list(session)
    return {"count": len(items), "themes": items}


class ThemeAdd(BaseModel):
    name: str


@router.post("/themes/add")
async def themes_add(body: ThemeAdd = Body(...)):
    """Добавить тему в канонический словарь."""
    async with AsyncSessionLocal() as session:
        return await service.add_theme(session, body.name)


class ThemeDelete(BaseModel):
    name: str
    reassign_to: str


@router.post("/themes/delete")
async def themes_delete(body: ThemeDelete = Body(...)):
    """Удалить тему, перенеся её посты в другую (посты не теряются)."""
    async with AsyncSessionLocal() as session:
        return await service.delete_theme(session, body.name, body.reassign_to)


@router.get("/themes/aliases")
async def theme_aliases():
    """Синонимы тем (миграция 079): какое написание в какой канон сводится."""
    async with AsyncSessionLocal() as session:
        items = await service.aliases_list(session)
    return {"count": len(items), "aliases": items}


class AliasAdd(BaseModel):
    alias: str
    canon: str


@router.post("/themes/aliases/add")
async def theme_aliases_add(body: AliasAdd = Body(...)):
    """Завести синоним темы. Канон обязан существовать в словаре."""
    async with AsyncSessionLocal() as session:
        return await service.add_alias(session, body.alias, body.canon)


class AliasDelete(BaseModel):
    alias: str


@router.post("/themes/aliases/delete")
async def theme_aliases_delete(body: AliasDelete = Body(...)):
    """Убрать синоним. Уже записанные вердикты не меняются."""
    async with AsyncSessionLocal() as session:
        return await service.delete_alias(session, body.alias)


@router.post("/themes/canonicalize")
async def themes_canonicalize():
    """Разово привести записанные вердикты к канону по текущему словарю.

    Чинит прошлое: нормализация на запись действует только на новые вердикты,
    а накопленные неканонические написания продолжают дробить agree-rate и
    дистилляцию. Идемпотентно; неизвестные написания не трогаются и
    возвращаются списком как кандидаты в синонимы.
    """
    async with AsyncSessionLocal() as session:
        return await service.canonicalize_existing(session)


@router.get("/health")
async def health():
    """Диагностика рутины: backlog по регионам, вердикты/сутки, покрытие потока.

    Плюс сквозная воронка за сутки (заказ владельца 2026-08-19): собрано →
    размечено → допустил/выкинул/отложил → дошло до читателя.
    """
    from config.classifier import get_pending_max, get_source_days

    async with AsyncSessionLocal() as session:
        out = await service.health_stats(session, days=get_source_days())
        out["funnel"] = await service.funnel_stats(session, hours=24)
    out["pending_max"] = get_pending_max()
    return out


@router.get("/progress")
async def progress():
    """Насколько оператор отстал от движка: вынесено / разобрано / осталось / темп."""
    async with AsyncSessionLocal() as session:
        return await service.operator_progress_stats(session, hours=24)


@router.get("/feed/grouped")
async def feed_grouped(
    region: str = Query("", description="код района (опционально)"),
    limit: int = Query(5000, ge=1, le=20000),
    cards_per_block: int = Query(40, ge=1, le=500),
):
    """Лента блоками «вердикт × тема» с схлопнутыми дословными дублями.

    ``limit`` — сколько постов очереди берём в расчёт (счётчики блоков),
    ``cards_per_block`` — сколько карточек рисуем внутри блока. Числа разные:
    заголовок и групповая кнопка обязаны говорить про весь блок, иначе кнопка
    «Согласен со всем блоком (267)» закрывала бы показанные сорок.
    """
    async with AsyncSessionLocal() as session:
        blocks = await service.review_feed_grouped(
            session,
            region_code=region.strip() or None,
            limit=limit,
            cards_per_block=cards_per_block,
        )
    return {"blocks": blocks, "count": sum(b["total"] for b in blocks)}


# --- Петля обучения (ADR-0005): оператор утверждает выученные правила ------------


@router.get("/rules")
async def rules_list(
    status: str = Query("", description="proposed|approved|rejected|retired; пусто = все"),
):
    """Выученные правила для операторской панели (черновики + утверждённые)."""
    async with AsyncSessionLocal() as session:
        items = await rules.list_rules(session, status=status.strip() or None)
    return {"count": len(items), "rules": items}


class RuleDecision(BaseModel):
    edited_text: Optional[str] = None  # опц. правка текста при утверждении


@router.post("/rules/{rule_id}/approve")
async def rule_approve(rule_id: int, body: RuleDecision = Body(default=RuleDecision())):
    """✅ Утвердить черновик правила (опц. с правкой текста) → в деле со след. прогона."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(
            session, rule_id, status="approved", edited_text=body.edited_text
        )


@router.post("/rules/{rule_id}/reject")
async def rule_reject(rule_id: int):
    """❌ Отклонить черновик правила."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(session, rule_id, status="rejected")


@router.post("/rules/{rule_id}/retire")
async def rule_retire(rule_id: int):
    """🗑 Вывести утверждённое правило из обращения (aging)."""
    async with AsyncSessionLocal() as session:
        return await rules.decide_rule(session, rule_id, status="retired")


class RuleAdd(BaseModel):
    rule_text: str
    region_code: Optional[str] = None


@router.post("/rules/add")
async def rule_add(body: RuleAdd = Body(...)):
    """Оператор пишет правило руками → сразу утверждено (в деле со след. прогона)."""
    async with AsyncSessionLocal() as session:
        return await rules.add_operator_rule(session, body.rule_text, region_code=body.region_code)
