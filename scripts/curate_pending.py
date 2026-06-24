#!/usr/bin/env python3
"""CLI для shadow LLM-курации сводок (PoC, письмо brain 2026-06-07).

Мост между БД (`bulletin_curation_runs`) и slash-командой /curate: команда
(Claude Code /loop) забирает pending-прогоны, по рубрике релевантности ставит
per-post вердикт keep/drop и пишет его назад. Все необратимые действия — за
детерминированным кодом (тут только UPDATE verdicts), черту #025/#027 не трогаем.

Запуск на проде (данные в прод-БД):
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/curate_pending.py --list"

Подкоманды:
  --list [--limit N] [--region CODE]   pending-прогоны как JSON (для рубрики)
  --apply [--file PATH]                 записать вердикты (JSON из файла/stdin):
       {"id": 12, "tokens_estimate": 800,
        "verdicts": [{"lip": "123_45", "verdict": "drop", "reason": "перефраз-дубль"}]}
  --stats [--region CODE]               агрегат для ack brain'у (flag-rate и т.п.)
  --flagged [--region CODE]            помеченные «drop» посты (text+url+причина) —
                                        выгрузка для precision-спот-чека владельцем
                                        (gate Фазы 2: ground-truth релевантности — он)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from sqlalchemy import select

# noqa: F401 — импорт регистрирует Region/прочие классы в SQLAlchemy-registry,
# иначе конфигурация мапперов падает на relationship ScheduledPublication.region.
from database import models  # noqa: F401
from database.connection import AsyncSessionLocal
from database.models_extended import BulletinCurationRun


async def _list(limit: int, region: str | None) -> None:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(BulletinCurationRun)
            .where(BulletinCurationRun.status == "pending")
            .order_by(BulletinCurationRun.created_at.asc())
            .limit(limit)
        )
        if region:
            stmt = stmt.where(BulletinCurationRun.region_code == region)
        rows = (await session.execute(stmt)).scalars().all()
        out = [
            {
                "id": r.id,
                "region_code": r.region_code,
                "theme": r.theme,
                "kind": r.kind,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "total_count": r.total_count,
                "published_url": r.published_url,
                "candidates": r.candidates or [],
            }
            for r in rows
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))


async def _apply(payload: dict) -> None:
    run_id = payload.get("id")
    verdicts = payload.get("verdicts") or []
    if not run_id or not isinstance(verdicts, list):
        raise SystemExit("apply: требуются поля 'id' (int) и 'verdicts' (list)")

    valid = {"keep", "drop"}
    norm = []
    for v in verdicts:
        verdict = str(v.get("verdict", "")).strip().lower()
        if verdict not in valid:
            raise SystemExit(f"apply: verdict должен быть keep|drop, получено {verdict!r}")
        norm.append(
            {
                "lip": str(v.get("lip", "")),
                "verdict": verdict,
                "reason": str(v.get("reason", "")).strip(),
            }
        )
    flagged = sum(1 for v in norm if v["verdict"] == "drop")

    async with AsyncSessionLocal() as session:
        row = (
            (
                await session.execute(
                    select(BulletinCurationRun).where(BulletinCurationRun.id == int(run_id))
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise SystemExit(f"apply: прогон id={run_id} не найден")
        # Идемпотентно: повторный apply перезаписывает вердикты и reviewed_at.
        row.verdicts = norm
        row.flagged_count = flagged
        row.tokens_estimate = payload.get("tokens_estimate")
        row.status = "reviewed"
        row.reviewed_at = datetime.utcnow()
        await session.commit()
    print(
        json.dumps(
            {"id": int(run_id), "reviewed": len(norm), "flagged": flagged},
            ensure_ascii=False,
        )
    )


def _flagged_from_run(candidates: list | None, verdicts: list | None) -> list[dict]:
    """Сджойнить drop-вердикты с текстом кандидата по `lip`. Чистая (без БД) →
    тестируемо. Пропавший кандидат (lip не нашёлся) деградирует в пустые поля,
    но строку не теряем — drop остаётся видимым для ревью."""
    by_lip = {c.get("lip"): c for c in (candidates or []) if isinstance(c, dict)}
    out: list[dict] = []
    for v in verdicts or []:
        if not isinstance(v, dict) or v.get("verdict") != "drop":
            continue
        cand = by_lip.get(v.get("lip")) or {}
        out.append(
            {
                "lip": v.get("lip"),
                "url": cand.get("url"),
                "reason": (v.get("reason") or "").strip(),
                "text": cand.get("text") or "",
                "has_media": bool(cand.get("has_media")),
            }
        )
    return out


async def _flagged(region: str | None) -> None:
    async with AsyncSessionLocal() as session:
        stmt = (
            select(BulletinCurationRun)
            .where(BulletinCurationRun.status == "reviewed")
            .order_by(BulletinCurationRun.created_at.asc())
        )
        if region:
            stmt = stmt.where(BulletinCurationRun.region_code == region)
        rows = (await session.execute(stmt)).scalars().all()

        out = []
        for r in rows:
            for f in _flagged_from_run(r.candidates, r.verdicts):
                out.append(
                    {
                        "run_id": r.id,
                        "region_code": r.region_code,
                        "theme": r.theme,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                        **f,
                    }
                )
        print(json.dumps(out, ensure_ascii=False, indent=2))


async def _stats(region: str | None) -> None:
    async with AsyncSessionLocal() as session:
        stmt = select(BulletinCurationRun).where(BulletinCurationRun.status == "reviewed")
        if region:
            stmt = stmt.where(BulletinCurationRun.region_code == region)
        rows = (await session.execute(stmt)).scalars().all()

        runs = len(rows)
        total = sum(r.total_count or 0 for r in rows)
        flagged = sum(r.flagged_count or 0 for r in rows)
        tokens = sum(r.tokens_estimate or 0 for r in rows)
        reasons: dict[str, int] = {}
        for r in rows:
            for v in r.verdicts or []:
                if v.get("verdict") == "drop":
                    key = (v.get("reason") or "—").strip() or "—"
                    reasons[key] = reasons.get(key, 0) + 1

        # pending для heartbeat #018 (видно «копится / встало»)
        pending = (
            (
                await session.execute(
                    select(BulletinCurationRun).where(BulletinCurationRun.status == "pending")
                )
            )
            .scalars()
            .all()
        )

        print(
            json.dumps(
                {
                    "reviewed_runs": runs,
                    "pending_runs": len(pending),
                    "candidates_total": total,
                    "flagged_drop": flagged,
                    "flag_rate": round(flagged / total, 4) if total else 0.0,
                    "tokens_estimate_total": tokens,
                    "drop_reasons": dict(
                        sorted(reasons.items(), key=lambda kv: kv[1], reverse=True)
                    ),
                },
                ensure_ascii=False,
                indent=2,
            )
        )


def main() -> None:
    ap = argparse.ArgumentParser(description="Shadow LLM-курация сводок (PoC)")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true", help="pending-прогоны как JSON")
    g.add_argument("--apply", action="store_true", help="записать вердикты (JSON)")
    g.add_argument("--stats", action="store_true", help="агрегат измерения PoC")
    g.add_argument(
        "--flagged", action="store_true", help="drop-посты (text+url+причина) для спот-чека"
    )
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--region", type=str, default=None)
    ap.add_argument("--file", type=str, default=None, help="JSON-файл для --apply (иначе stdin)")
    args = ap.parse_args()

    if args.list:
        asyncio.run(_list(args.limit, args.region))
    elif args.stats:
        asyncio.run(_stats(args.region))
    elif args.flagged:
        asyncio.run(_flagged(args.region))
    elif args.apply:
        raw = open(args.file, encoding="utf-8").read() if args.file else sys.stdin.read()
        asyncio.run(_apply(json.loads(raw)))


if __name__ == "__main__":
    main()
