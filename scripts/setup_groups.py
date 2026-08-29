"""Автооформление ИНФО-сообществ: описание, аватар, обложка, закреп-визитка.

Этап 2 ребрендинга (план 2026-08-29). По каждому активному району смотрим
живой снимок сообщества (``groups.getById``) и решаем режим:

- **full** — сообщество «голое» (нет аватара ∧ нет обложки ∧ описание короче
  240 знаков): ставим описание (community-ключ), аватар (user VALSTAN, с
  чисткой системного поста), обложку (community-ключ), публикуем и закрепляем
  пост-визитку. Всё — из ``modules/promotion``: branding + copy + group_setup_vk.
- **spot** — у сообщества авторское оформление (11 старых): программно НЕ
  трогаем ничего. Город и статус, которые у них тоже бывают кривые, через API
  не правятся вообще (ловушка #219, ``docs/ops/group-setup-probe.md``) — скрипт
  лишь печатает список «что поправить руками/браузером».

Идемпотентность — через ``promo_group_setup`` (region_id + setup_version
уникальны): повторный прогон пропускает применённые записи, правка дизайна =
бамп ``branding.TEMPLATE_VERSION``. ``before`` пишется до правок — описание
откатывается командой ``--rollback <code>`` (аватар/обложку откатывать некуда:
их не было).

Бюджет user-вызовов VALSTAN: ~5 на full-регион (upload-server, save, wall.get,
wall.delete, wall.pin) при лимите ``get_valstan_call_budget()`` — скрипт сам
останавливается, досчитав бюджет, продолжение — следующим прогоном (порции).

Запуск (на проде, под env приложения):
    python scripts/setup_groups.py                      # dry-run, отчёт
    python scripts/setup_groups.py --apply              # правки (гейт #025!)
    python scripts/setup_groups.py --apply --regions uni,falenki
    python scripts/setup_groups.py --rollback oparino   # вернуть описание
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("setup_groups")

# Порог «описание шаблонное»: у 25 голых групп это 200-230 знаков болванки
# из чек-листа создания; авторские описания старых — 500+.
TEMPLATE_DESC_MAX = 240

# Сколько user-вызовов VALSTAN стоит один full-регион (см. docstring).
USER_CALLS_PER_REGION = 5


def _genitive_from_zagolovki(zagolovki: Optional[dict]) -> Optional[str]:
    """«Новости Опаринского округа:» → «Опаринского округа»."""
    if not isinstance(zagolovki, dict):
        return None
    raw = (zagolovki.get("novost") or "").strip()
    if raw.lower().startswith("новости ") and raw.endswith(":"):
        return raw[len("новости ") : -1].strip()
    return None


async def load_targets(codes: Optional[List[str]]) -> List[dict]:
    """Активные районы с главной группой + брендинг-данные из region_configs."""
    from sqlalchemy import text

    from database.connection import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT r.id, r.code, r.name, r.neighbors, r.vk_group_id, "
                    "       rc.zagolovki "
                    "FROM regions r "
                    "LEFT JOIN region_configs rc ON rc.region_code = r.code "
                    "WHERE r.is_active IS TRUE AND r.vk_group_id IS NOT NULL "
                    "  AND r.kind = 'raion' "
                    "ORDER BY r.code"
                )
            )
        ).fetchall()
    out = []
    for r in rows:
        if codes and r.code not in codes:
            continue
        out.append(
            {
                "region_id": r.id,
                "code": r.code,
                "name": r.name,
                "neighbors": r.neighbors,
                "vk_group_id": r.vk_group_id,
                "zagolovki": r.zagolovki,
            }
        )
    return out


async def load_tokens() -> Tuple[Optional[str], Dict[int, str]]:
    from modules.vk_token_router import load_vk_routing

    return await load_vk_routing()


async def claim_setup(region_id: int, version: int) -> Optional[int]:
    """Идемпотентный insert в promo_group_setup; None = уже применено."""
    from sqlalchemy import select
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from database.connection import AsyncSessionLocal
    from database.models import PromoGroupSetup

    async with AsyncSessionLocal() as session:
        stmt = (
            pg_insert(PromoGroupSetup)
            .values(region_id=region_id, setup_version=version, status="dry_run")
            .on_conflict_do_nothing(index_elements=["region_id", "setup_version"])
            .returning(PromoGroupSetup.id)
        )
        row = (await session.execute(stmt)).scalar()
        if row is not None:
            await session.commit()
            return int(row)
        existing = (
            await session.execute(
                select(PromoGroupSetup.id, PromoGroupSetup.status).where(
                    PromoGroupSetup.region_id == region_id,
                    PromoGroupSetup.setup_version == version,
                )
            )
        ).first()
        await session.commit()
        if existing and existing.status == "applied":
            return None  # уже сделано этой версией шаблона
        return int(existing.id) if existing else None


async def finish_setup(
    setup_id: int,
    *,
    status: str,
    before: Optional[dict] = None,
    after: Optional[dict] = None,
    applied_fields: Optional[list] = None,
    pinned_post_url: Optional[str] = None,
    vk_error_code: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    from sqlalchemy import update

    from database.connection import AsyncSessionLocal
    from database.models import PromoGroupSetup

    async with AsyncSessionLocal() as session:
        await session.execute(
            update(PromoGroupSetup)
            .where(PromoGroupSetup.id == setup_id)
            .values(
                status=status,
                before=before,
                after=after,
                applied_fields=applied_fields,
                pinned_post_url=pinned_post_url,
                vk_error_code=vk_error_code,
                error=error,
            )
        )
        await session.commit()


def build_texts(target: dict) -> dict:
    """Все тексты и картинки региона: описание, визитка, аватар, обложка."""
    from config.promo import get_network_list_url
    from modules.promotion.branding import default_tagline, render_avatar, render_cover
    from modules.promotion.copy import render_group_description, render_welcome_post
    from modules.region_links import base_title, parse_neighbors

    title = base_title(target["name"], None)
    genitive = _genitive_from_zagolovki(target["zagolovki"]) or f"района ({title})"
    site_url = get_network_list_url()

    neighbors = []
    for code in parse_neighbors(target.get("neighbors"))[:5]:
        info = target.get("_neighbor_index", {}).get(code)
        if info:
            neighbors.append(info)

    return {
        "title": title,
        "description": render_group_description(
            district_name=genitive, center_city=title, site_url=site_url
        ),
        "welcome": render_welcome_post(
            district_name=genitive, neighbors=neighbors, site_url=site_url
        ),
        "avatar": render_avatar(target["code"], title),
        "cover": render_cover(target["code"], title, default_tagline(genitive.capitalize())),
        "genitive": genitive,
    }


def neighbor_index(targets: List[dict]) -> Dict[str, dict]:
    from modules.region_links import base_title, community_url

    out = {}
    for t in targets:
        url = community_url(t["vk_group_id"], None)
        if url:
            out[t["code"]] = {"name": f"{base_title(t['name'], None)} ИНФО", "url": url}
    return out


async def rollback(code: str) -> int:
    """Вернуть описание региона из последнего ``before`` в promo_group_setup."""
    import vk_api
    from sqlalchemy import select, text

    from database.connection import AsyncSessionLocal
    from database.models import PromoGroupSetup
    from modules.promotion.group_setup_vk import edit_description
    from modules.vk_token_router import load_community_tokens

    async with AsyncSessionLocal() as session:
        region = (
            await session.execute(
                text("SELECT id, vk_group_id FROM regions WHERE code = :c"), {"c": code}
            )
        ).first()
        if not region:
            logger.error("Регион %s не найден", code)
            return 2
        row = (
            (
                await session.execute(
                    select(PromoGroupSetup)
                    .where(PromoGroupSetup.region_id == region.id)
                    .order_by(PromoGroupSetup.setup_version.desc())
                )
            )
            .scalars()
            .first()
        )
        if not row or not row.before:
            logger.error("Нет сохранённого before для %s — откатывать нечего", code)
            return 2
        community_tokens = await load_community_tokens(session)

    token = community_tokens.get(abs(int(region.vk_group_id)))
    if not token:
        logger.error("Нет community-ключа для %s", code)
        return 2
    api = vk_api.VkApi(token=token).get_api()
    result = edit_description(api, region.vk_group_id, row.before.get("description") or "")
    logger.info("Откат описания %s: %s", code, "ok" if result.ok else result.detail)
    await finish_setup(row.id, status="rolled_back", before=row.before, after=row.after)
    return 0 if result.ok else 1


def process_region(
    target: dict,
    *,
    apply: bool,
    user_api,
    community_api,
    out_dir: Optional[str],
) -> Tuple[str, str, Optional[dict]]:
    """Один регион: снимок → режим → (dry-run печать | правки). Синхронно.

    Возвращает (mode, summary, report) — report непустой при apply.
    """
    from modules.promotion.group_setup_vk import (
        edit_description,
        get_current,
        pin_post,
        post_welcome,
        upload_avatar,
        upload_cover,
    )

    gid = target["vk_group_id"]
    snap = get_current(community_api or user_api, gid)
    if not snap.ok:
        return "error", f"снимок не взялся: {snap.detail}", None
    cur = snap.payload or {}

    bare = (
        not cur.get("has_photo")
        and not cur.get("has_cover")
        and len(cur.get("description") or "") <= TEMPLATE_DESC_MAX
    )
    mode = "full" if bare else "spot"
    texts = build_texts(target)

    if out_dir:
        os.makedirs(out_dir, exist_ok=True)
        with open(os.path.join(out_dir, f"{target['code']}_avatar.jpg"), "wb") as fh:
            fh.write(texts["avatar"])
        with open(os.path.join(out_dir, f"{target['code']}_cover.jpg"), "wb") as fh:
            fh.write(texts["cover"])

    if mode == "spot":
        gaps = []
        if not (cur.get("status") or "").strip():
            gaps.append("статус пуст")
        if (cur.get("city") or "") in ("", "Калинино"):
            gaps.append(f"город «{cur.get('city')}» — руками")
        return mode, "; ".join(gaps) or "всё на месте", None

    if not apply:
        return (
            mode,
            f"desc {len(cur.get('description') or '')}→{len(texts['description'])} зн., "
            f"аватар+обложка+визитка; город «{cur.get('city')}» — руками",
            None,
        )

    if community_api is None:
        return "error", "нет community-ключа — full-режим невозможен", None

    report: dict = {"before": cur, "applied": [], "errors": []}
    steps = [
        ("description", lambda: edit_description(community_api, gid, texts["description"])),
        ("avatar", lambda: upload_avatar(user_api, gid, texts["avatar"])),
        ("cover", lambda: upload_cover(community_api, gid, texts["cover"])),
    ]
    for field, action in steps:
        res = action()
        if res.ok:
            report["applied"].append(field)
        else:
            report["errors"].append(f"{field}: [{res.vk_error_code}] {res.detail}")
        time.sleep(interval())

    posted = post_welcome(community_api, gid, texts["welcome"])
    if posted.ok:
        post_id = posted.payload["post_id"]
        report["applied"].append("welcome_post")
        report["post_url"] = f"https://vk.ru/wall-{abs(int(gid))}_{post_id}"
        time.sleep(interval())
        pinned = pin_post(user_api, gid, post_id)
        if pinned.ok:
            report["applied"].append("pin")
        else:
            report["errors"].append(f"pin: [{pinned.vk_error_code}] {pinned.detail}")
    else:
        report["errors"].append(f"welcome: [{posted.vk_error_code}] {posted.detail}")

    summary = f"применено: {', '.join(report['applied']) or 'ничего'}"
    if report["errors"]:
        summary += f"; ошибки: {'; '.join(report['errors'])}"
    return mode, summary, report


def interval() -> float:
    from config.promo import get_post_interval_seconds

    return get_post_interval_seconds()


async def amain() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="выполнить правки (гейт #025)")
    parser.add_argument("--regions", default=None, help="коды через запятую")
    parser.add_argument("--rollback", default=None, metavar="CODE", help="откат описания")
    parser.add_argument("--out", default=None, help="куда сложить превью картинок")
    args = parser.parse_args()

    from modules.secrets_bootstrap import bootstrap_secrets

    bootstrap_secrets()

    if args.rollback:
        return await rollback(args.rollback)

    import vk_api

    from config.promo import get_valstan_call_budget
    from modules.promotion.branding import TEMPLATE_VERSION

    codes = [c.strip() for c in args.regions.split(",")] if args.regions else None
    targets = await load_targets(codes)
    if not targets:
        logger.error("Нет целей")
        return 2

    all_targets = await load_targets(None)
    nb_index = neighbor_index(all_targets)
    for t in targets:
        t["_neighbor_index"] = nb_index

    user_token, community_tokens = await load_tokens()
    user_api = vk_api.VkApi(token=user_token).get_api() if user_token else None
    if args.apply and user_api is None:
        logger.error("Нет user-токена — аватары и закреп невозможны")
        return 2

    budget = get_valstan_call_budget()
    budget_regions = max(1, budget // USER_CALLS_PER_REGION)
    done_full = 0

    logger.info(
        "Регионов: %d | режим: %s | шаблон v%d | бюджет VALSTAN: %d вызовов (~%d full-регионов)",
        len(targets),
        "APPLY" if args.apply else "dry-run",
        TEMPLATE_VERSION,
        budget,
        budget_regions,
    )

    for target in targets:
        gid = abs(int(target["vk_group_id"]))
        comm_token = community_tokens.get(gid)
        community_api = vk_api.VkApi(token=comm_token).get_api() if comm_token else None

        setup_id = None
        if args.apply:
            setup_id = await claim_setup(target["region_id"], TEMPLATE_VERSION)
            if setup_id is None:
                logger.info(
                    "  %-14s ✅ уже применено (v%d) — пропуск", target["code"], TEMPLATE_VERSION
                )
                continue
            if done_full >= budget_regions:
                logger.info("  %-14s ⏸ бюджет VALSTAN исчерпан — продолжить завтра", target["code"])
                continue

        mode, summary, report = await asyncio.to_thread(
            process_region,
            target,
            apply=args.apply,
            user_api=user_api,
            community_api=community_api,
            out_dir=args.out,
        )
        icon = {"full": "🛠", "spot": "👁", "error": "⛔"}.get(mode, "·")
        logger.info("  %-14s %s %-5s %s", target["code"], icon, mode, summary)

        if args.apply and setup_id is not None and report is not None:
            ok = not report["errors"]
            await finish_setup(
                setup_id,
                status="applied" if ok else "error",
                before=report.get("before"),
                after={"applied": report["applied"]},
                applied_fields=report["applied"],
                pinned_post_url=report.get("post_url"),
                error="; ".join(report["errors"]) or None,
            )
            if mode == "full":
                done_full += 1
        elif args.apply and setup_id is not None:
            # spot/error: запись не нужна — снимаем claim, чтобы не висел dry_run
            await finish_setup(setup_id, status="dry_run", error=f"{mode}: {summary}")

    logger.info("\nГотово. Город и статус правятся только в веб-интерфейсе (см. отчёт spot).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
