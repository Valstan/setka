#!/usr/bin/env python3
"""Активация районов: скелет → живой регион (проверка готовности + запись).

Скелетные районы Кировской области лежат в БД с ``is_active=false`` и пустым
``vk_group_id``: пул источников засеян, брендинг и localities готовы, не хватает
только VK-группы, куда публиковать сводки. Её создаёт владелец руками
(порциями 2–4/день — антиспам VK), после чего район надо «включить».

Раньше включение было россыпью ручных шагов: узнать id группы, `UPDATE
vk_group_id`, `UPDATE is_active`, не забыть neighbors, проверить, что
community-токен заведён. Каждый шаг помнить обязан человек, а забытый шаг даёт
тихий отказ — район активен, но сводка ему не уходит (или уходит без соседского
обмена).

Скрипт сводит это в одну команду и **по умолчанию ничего не пишет**: печатает
отчёт готовности по каждому району. Запись — только с ``--apply``.

    # проверить, что готово (ничего не меняет)
    python scripts/activate_regions.py omutninsk belholunitsa yurya verhnekame

    # то же, но с записью в БД (прод — под человеческим гейтом #025)
    python scripts/activate_regions.py omutninsk --apply

    # если короткий адрес группы не совпадает с кодом района
    python scripts/activate_regions.py verhnekame=kirs_info43 --apply
    python scripts/activate_regions.py verhnekame=-240386781 --apply

Блокеры (район НЕ активируется): группы нет в VK, регион не найден, пустой пул
источников, регион уже активен с другим ``vk_group_id`` (без ``--force``).
Предупреждения (активация идёт, но требуют внимания): нет community-токена, не
заполнены neighbors, у токена нет прав администратора в группе.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # pragma: no cover — только для аннотаций
    from database.models import Region

# БД и VK импортируются лениво, внутри функций: чистые решения этого модуля
# (parse_target / evaluate_readiness) должны грузиться в тестах без Postgres,
# Redis и токенов — как у остальных CLI-скриптов проекта.

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# Короткий адрес группы по умолчанию — как у батча №1 (yaransk_info43 и др.).
DEFAULT_SCREEN_NAME_SUFFIX = "_info43"


@dataclass
class Target:
    """Что активируем: код района + как искать его группу в VK."""

    code: str
    screen_name: Optional[str] = None
    group_id: Optional[int] = None  # отрицательный, как хранится в regions


@dataclass
class Readiness:
    """Вердикт по одному району: что нашли и можно ли включать."""

    code: str
    group_id: Optional[int] = None
    group_title: Optional[str] = None
    pool_size: int = 0
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ready(self) -> bool:
        return not self.blockers


def parse_target(raw: str) -> Target:
    """``omutninsk`` | ``verhnekame=kirs_info43`` | ``verhnekame=-240386781``."""
    code, _, hint = raw.partition("=")
    code = code.strip()
    if not code:
        raise ValueError(f"пустой код региона в аргументе '{raw}'")
    hint = hint.strip()
    if not hint:
        return Target(code=code, screen_name=code + DEFAULT_SCREEN_NAME_SUFFIX)
    # Числовой хинт — это id группы; принимаем и со знаком, и без.
    if hint.lstrip("-").isdigit():
        return Target(code=code, group_id=-abs(int(hint)))
    return Target(code=code, screen_name=hint)


def evaluate_readiness(
    *,
    target: Target,
    region: Optional[Region],
    group_id: Optional[int],
    group_title: Optional[str],
    pool_size: int,
    has_community_token: bool,
    neighbors: Optional[str],
    token_is_admin: Optional[bool],
    force: bool,
) -> Readiness:
    """Чистое решение «включать / не включать» по собранным фактам.

    Отделено от IO намеренно: именно здесь легко ошибиться, и именно это
    покрыто тестами. Блокер = активация не даст работающий регион.
    Предупреждение = регион заработает, но частью функций (соседский обмен,
    публикация от имени сообщества) — не сразу.
    """
    verdict = Readiness(code=target.code, group_id=group_id, group_title=group_title)

    if region is None:
        verdict.blockers.append(f"региона '{target.code}' нет в БД")
        return verdict

    verdict.pool_size = pool_size

    if group_id is None:
        hint = target.screen_name or (str(target.group_id) if target.group_id else "?")
        verdict.blockers.append(f"группа не найдена в VK (искали '{hint}') — сначала создать")
    if pool_size == 0:
        verdict.blockers.append("пул источников пуст — активация даст пустые сводки")
    if (
        region.is_active
        and region.vk_group_id
        and group_id is not None
        and region.vk_group_id != group_id
        and not force
    ):
        verdict.blockers.append(
            f"регион уже активен с другой группой ({region.vk_group_id}) — "
            "проверить вручную, перезаписать можно через --force"
        )

    # Предупреждения про саму группу имеют смысл, только когда группа найдена:
    # иначе «нет ключа сообщества» и «токен не админ» — механическое следствие
    # её отсутствия, и они лишь топят единственный настоящий блокер в шуме.
    if group_id is not None:
        if not has_community_token:
            verdict.warnings.append(
                "нет community-токена COMM_<id> — публикация пойдёт каскадом "
                "(VALSTAN → МАМА), выпустить ключ может только владелец группы"
            )
        if token_is_admin is False:
            verdict.warnings.append(
                "читающий токен не администрирует группу — проверить, что публикующий аккаунт "
                "имеет права в ней"
            )

    if not (neighbors or "").strip():
        verdict.warnings.append(
            "neighbors пусты — соседский обмен новостями работать не будет "
            "(миграция 073 заполняет их для всей области)"
        )

    return verdict


def _resolve_group(vk: Any, target: Target) -> tuple[Optional[int], Optional[str]]:
    """(group_id, title) по screen_name или прямому id. None — группы нет."""
    group_id = target.group_id
    if group_id is None and target.screen_name:
        try:
            resolved = vk.utils.resolveScreenName(screen_name=target.screen_name)
        except Exception as exc:  # сеть/лимиты — не валим остальные районы
            logger.warning("resolveScreenName('%s') упал: %s", target.screen_name, exc)
            return None, None
        if not resolved or resolved.get("type") != "group":
            return None, None
        group_id = -abs(int(resolved["object_id"]))

    if group_id is None:
        return None, None

    try:
        info = vk.groups.getById(group_id=abs(group_id), fields="members_count")
        group = info[0] if isinstance(info, list) else info["groups"][0]
        return group_id, str(group.get("name"))
    except Exception as exc:
        logger.warning("groups.getById(%s) упал: %s", group_id, exc)
        return group_id, None


async def _collect_facts(session: Any, target: Target, vk: Any, admin_ids: set[int]):
    from sqlalchemy import func, select

    from database.models import Community, Region, VKToken

    region = (
        await session.execute(select(Region).where(Region.code == target.code))
    ).scalar_one_or_none()

    group_id, group_title = _resolve_group(vk, target)

    pool_size = 0
    if region is not None:
        pool_size = (
            await session.execute(
                select(func.count(Community.id)).where(Community.region_id == region.id)
            )
        ).scalar_one()

    has_token = False
    if group_id is not None:
        has_token = (
            await session.execute(
                select(func.count(VKToken.id)).where(
                    VKToken.community_id == abs(group_id), VKToken.is_active.is_(True)
                )
            )
        ).scalar_one() > 0

    token_is_admin = None if (not admin_ids or group_id is None) else (group_id in admin_ids)

    return (
        evaluate_readiness(
            target=target,
            region=region,
            group_id=group_id,
            group_title=group_title,
            pool_size=pool_size,
            has_community_token=has_token,
            neighbors=region.neighbors if region else None,
            token_is_admin=token_is_admin,
            force=False,
        ),
        region,
    )


def _report(verdict: Readiness) -> None:
    head = "✅" if verdict.ready else "⛔"
    title = verdict.group_title or "—"
    logger.info(
        "%s %s | группа: %s (%s) | пул: %d",
        head,
        verdict.code,
        title,
        verdict.group_id if verdict.group_id else "нет",
        verdict.pool_size,
    )
    for blocker in verdict.blockers:
        logger.info("     ⛔ %s", blocker)
    for warning in verdict.warnings:
        logger.info("     ⚠️  %s", warning)


async def activate(raw_targets: list[str], *, apply: bool, force: bool) -> int:
    import vk_api
    from sqlalchemy import update

    from database.connection import AsyncSessionLocal
    from database.models import Region
    from modules.vk_token_router import get_active_parse_tokens, get_healthy_read_token

    targets = [parse_target(raw) for raw in raw_targets]

    token = await get_healthy_read_token()
    if not token:
        logger.error("❌ Нет живого READ-токена VK — активацию проверить нечем")
        return 1
    vk = vk_api.VkApi(token=token).get_api()

    failed = 0
    async with AsyncSessionLocal() as session:
        # Админство собираем по ВСЕМ активным токенам, а не по одному READ-токену,
        # который выдал роутер. Группы заводит владелец под своим аккаунтом, а
        # читать может резервный (МАМА/VITA) — на первой же живой раскатке
        # 2026-07-26 это дало ложное «токен не администрирует группу» на всех
        # четырёх районах сразу, хотя права были у VALSTAN.
        admin_ids: set[int] = set()
        for name, candidate in (await get_active_parse_tokens(session)).items():
            try:
                admin = (
                    vk_api.VkApi(token=candidate).get_api().groups.get(filter="admin", count=200)
                )
                admin_ids |= {-abs(int(gid)) for gid in admin.get("items", [])}
            except Exception as exc:  # не блокер: просто не проверим этот токен
                logger.warning("groups.get(filter=admin) под %s упал: %s", name, exc)

        for target in targets:
            verdict, region = await _collect_facts(session, target, vk, admin_ids)
            if force:
                verdict.blockers = [b for b in verdict.blockers if "--force" not in b]
            _report(verdict)

            if not verdict.ready:
                failed += 1
                continue
            if not apply:
                logger.info("     (dry-run: без --apply ничего не записано)")
                continue

            await session.execute(
                update(Region)
                .where(Region.id == region.id)
                .values(vk_group_id=verdict.group_id, is_active=True)
            )
            await session.commit()
            logger.info("     ✅ активирован: vk_group_id=%s, is_active=true", verdict.group_id)

    if not apply:
        logger.info("Проверка завершена. Запись — тем же вызовом с --apply.")
    return 1 if failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Проверить готовность и активировать скелетные районы",
        epilog="Аргумент: код района, опционально =screen_name или =group_id.",
    )
    parser.add_argument("targets", nargs="+", help="omutninsk | verhnekame=kirs_info43")
    parser.add_argument(
        "--apply", action="store_true", help="записать в БД (без флага — только отчёт)"
    )
    parser.add_argument(
        "--force", action="store_true", help="перезаписать vk_group_id у активного региона"
    )
    args = parser.parse_args()

    try:
        return asyncio.run(activate(args.targets, apply=args.apply, force=args.force))
    except Exception as exc:
        logger.error("❌ Критическая ошибка: %s", exc, exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
