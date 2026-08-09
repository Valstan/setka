"""Отбор постов для сайта-получателя — вход конвейера (D-015).

Источник — **пересечение двух журналов**, а не один из них (решение владельца
2026-08-09, обоснование в ``config/content_conveyor``):

- ``bulletin_curation_runs`` с непустым ``published_post_id`` даёт признак
  «пост вошёл в опубликованную сводку района», то есть прошёл отбор, за который
  уже отвечает человек; сам текст там — снапшот кандидата;
- ``collected_post_audit`` (ADR-0004) даёт по тому же ``lip`` чистый текст поста
  и сводку вложений с прямыми ссылками на фото — то, что сайт переложит к себе.

Брать текст из аудита, а не из кандидата сводки, важно не из аккуратности:
кандидат — снимок для ленты классификатора, а аудит хранит ``media`` (миграция
060). Без него доставка знала бы, что вложения есть, но не где они лежат.

Уже обработанное отсеивается по ``conveyor_deliveries`` (site, lip): повторный
прогон не должен платить за LLM второй раз. Дедуп на стороне сайта (по
``vkPostId``) эту нашу трату не покрывает — он защищает его коллекцию, не наш счёт.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from sqlalchemy import select

from database.models_extended import BulletinCurationRun, CollectedPostAudit, ConveyorDelivery

logger = logging.getLogger(__name__)


def _published_lips(
    runs: Sequence[BulletinCurationRun], skip_themes: Sequence[str]
) -> Dict[str, str]:
    """map lip → тема сводки, в которую пост вошёл (новейшая запись выигрывает).

    Тема берётся у сводки, а не у поста: она и есть решение сборщика о том, чем
    этот пост был в ленте района, и по ней работает дешёвый префильтр ``skip_themes``.
    """
    skip = {t.strip().lower() for t in (skip_themes or ()) if str(t).strip()}
    out: Dict[str, str] = {}
    for run in runs:
        theme = (run.theme or "").strip().lower()
        if theme in skip:
            continue
        cands = run.candidates or []
        if not isinstance(cands, (list, tuple)):
            continue
        for c in cands:
            if not isinstance(c, dict):
                continue
            lip = str(c.get("lip") or "").strip()
            if not lip or lip in out:
                continue
            out[lip] = theme
    return out


async def _delivered_lips(session, *, site: str) -> set:
    """lip'ы, у которых для этого сайта уже есть строка журнала — в любом статусе.

    Отсекаем и ``rejected``/``held``: пост, однажды признанный неподходящим,
    не должен возвращаться в следующий прогон и снова стоить вызова. Пересмотр
    такого решения — операторское действие над строкой журнала, а не побочный
    эффект расписания.
    """
    rows = (
        await session.execute(select(ConveyorDelivery.lip).where(ConveyorDelivery.site == site))
    ).all()
    return {r[0] for r in rows}


async def fetch_pending_for_site(
    session,
    site: Dict[str, Any],
    *,
    days: int = 3,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """Посты, готовые к классификации для этого сайта — новейшие первыми.

    Возвращает список ``{lip, text, url, media, theme, region_code}``. Пост без
    текста пропускается: отдавать LLM пустую строку незачем, а на сайт такой
    материал всё равно не пойдёт.
    """
    site_key = str(site.get("key") or "").strip().lower()
    region = str(site.get("source_region") or "").strip()
    if not site_key or not region:
        return []

    cutoff = datetime.utcnow() - timedelta(days=max(1, days))
    runs = (
        (
            await session.execute(
                select(BulletinCurationRun)
                .where(BulletinCurationRun.region_code == region)
                .where(BulletinCurationRun.published_post_id.isnot(None))
                .where(BulletinCurationRun.created_at >= cutoff)
                .order_by(BulletinCurationRun.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    lip_theme = _published_lips(runs, site.get("skip_themes") or ())
    if not lip_theme:
        return []

    done = await _delivered_lips(session, site=site_key)
    wanted = [lip for lip in lip_theme if lip not in done]
    if not wanted:
        return []

    rows = (
        (
            await session.execute(
                select(CollectedPostAudit)
                .where(CollectedPostAudit.lip.in_(wanted))
                .order_by(CollectedPostAudit.collected_at.desc())
            )
        )
        .scalars()
        .all()
    )

    out: List[Dict[str, Any]] = []
    seen: set = set()
    for r in rows:
        if r.lip in seen:
            continue
        seen.add(r.lip)
        text = (r.post_text or "").strip()
        if not text:
            continue
        out.append(
            {
                "lip": r.lip,
                "text": text,
                "url": r.post_url or "",
                "media": r.media or [],
                "theme": lip_theme.get(r.lip, ""),
                "region_code": r.region_code,
            }
        )
        if len(out) >= max(1, limit):
            break
    return out


async def record_selection(
    session,
    *,
    site: str,
    lips: Sequence[str],
) -> int:
    """Завести строки журнала со статусом ``selected``. Возвращает число созданных.

    Идемпотентно: существующие ``(site, lip)`` пропускаются. Коммит — на
    вызывающем, чтобы отбор и первый шаг обработки ложились одной транзакцией.
    """
    site_key = (site or "").strip().lower()
    # ``x is not None`` до ``str(x)``: иначе None превращается в строку "None",
    # проходит проверку на непустоту и заводит строку журнала-призрак.
    fresh = [str(x).strip() for x in (lips or []) if x is not None and str(x).strip()]
    if not site_key or not fresh:
        return 0
    existing = {
        r[0]
        for r in (
            await session.execute(
                select(ConveyorDelivery.lip)
                .where(ConveyorDelivery.site == site_key)
                .where(ConveyorDelivery.lip.in_(fresh))
            )
        ).all()
    }
    added = 0
    for lip in fresh:
        if lip in existing:
            continue
        existing.add(lip)
        session.add(ConveyorDelivery(site=site_key, lip=lip, status="selected"))
        added += 1
    return added


async def update_delivery(
    session,
    *,
    site: str,
    lip: str,
    status: str,
    reason: Optional[str] = None,
    verdict: Optional[Dict[str, Any]] = None,
    attempts: Optional[int] = None,
    http_status: Optional[int] = None,
    remote_id: Optional[str] = None,
) -> bool:
    """Обновить строку журнала. ``False`` — строки нет (отбор её не заводил).

    Пишем только переданные поля: прогон доставки не должен затирать вердикт,
    добытый предыдущим шагом, лишь потому, что не знает о нём.
    """
    row = (
        await session.execute(
            select(ConveyorDelivery)
            .where(ConveyorDelivery.site == (site or "").strip().lower())
            .where(ConveyorDelivery.lip == (lip or "").strip())
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    row.status = status
    if reason is not None:
        row.reason = reason[:64]
    if verdict is not None:
        row.verdict = verdict
    if attempts is not None:
        row.attempts = attempts
    if http_status is not None:
        row.http_status = http_status
    if remote_id is not None:
        row.remote_id = remote_id[:100]
    return True


async def site_status_counts(session, *, site: str) -> Dict[str, int]:
    """Сводка журнала по статусам для сайта — основа метрики и разбора «почему нет на сайте»."""
    from sqlalchemy import func

    rows = (
        await session.execute(
            select(ConveyorDelivery.status, func.count())
            .where(ConveyorDelivery.site == (site or "").strip().lower())
            .group_by(ConveyorDelivery.status)
        )
    ).all()
    return {str(s): int(n) for s, n in rows}


def summarize_for_prompt(post: Dict[str, Any], *, max_chars: int = 1500) -> Optional[str]:
    """Компактное представление поста для LLM-вызова. ``None`` — подавать нечего.

    Обрезка по символам, а не по токенам, намеренно: точный счёт токенов требует
    токенизатора модели, а нам нужен предохранитель от аномально длинного поста,
    и грубой границы для него достаточно.
    """
    text = (post.get("text") or "").strip()
    if not text:
        return None
    media = post.get("media") or []
    kinds = sorted({str(m.get("type")) for m in media if isinstance(m, dict) and m.get("type")})
    head = f"[тема сводки: {post.get('theme') or '—'}; вложения: {', '.join(kinds) or 'нет'}]"
    return f"{head}\n{text[:max_chars]}"
