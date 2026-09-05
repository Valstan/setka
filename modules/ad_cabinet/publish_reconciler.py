"""Авто-фиксация публикаций отложки рекламного кабинета (PR-6, замыкание цикла).

Оператор ставит рекламу в VK-«Отложенные» (блок B). VK публикует сам в
назначенное время — но кабинет об этом не узнавал: статус оставался
``scheduled``, публикация и оплата не фиксировались, пока оператор не отметит
вручную. Этот реконсилер раз в полчаса проверяет отложки, чьё время прошло, и
для опубликованных вызывает :func:`record_published`:

  * ``AdScheduledPost.status`` → ``published``;
  * создаёт ``AdPublication`` (факт выхода);
  * если у отложки есть ``client_id`` и ``price`` — создаёт ``AdPayment`` со
    статусом ``awaiting`` (деньги ждём — owner так решил);
  * двигает клиента в воронке в ``published`` (не понижая paid/lost);
  * пишет событие ``published`` (actor='system') в таймлайн.

:func:`record_published` — ЕДИНСТВЕННАЯ точка фиксации выхода: её же зовёт
диспетчер репостов планировщика предложки (``repost_dispatcher``), который
публикует сам и не ждёт этого beat'а. Одна правда об учёте на оба пути.

Идемпотентность: выбираются только ``status='scheduled'`` — после перевода в
``published`` строка повторно не попадёт, дублей не будет. Строки-репосты
(``kind='repost'``) сюда не попадают по построению: у них ``vk_postponed_post_id``
пуст до выхода, а выходом занимается диспетчер.

VK-проверка вынесена в инъектируемый ``is_published(owner_id, post_id)`` →
``True`` (вышел) | ``False`` (ещё в отложке) | ``None`` (неизвестно/удалён),
чтобы чистую логику реконсиляции можно было покрыть тестами без сети.
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, Optional, Sequence

from sqlalchemy import select

from database.models import AdClient, AdPayment, AdPublication, AdRequest, AdScheduledPost
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

MSK = timezone(timedelta(hours=3))


def now_msk() -> datetime:
    """«Сейчас» в той же шкале, что ``AdScheduledPost.publish_date`` — МСК wall-clock naive.

    До 2026-09-05 реконсилер сравнивал МСК-дату публикации с ``datetime.utcnow()``
    (аудит кабинета): фиксация выхода, awaiting-платёж и «Ваш пост вышел»
    опаздывали ровно на три часа, а с окном beat 8–22 вечерние посты ждали до
    утра. Образец — ``post_expirer.run_expiry``.
    """
    return datetime.now(MSK).replace(tzinfo=None)


def _build_default_checker(user_token: str, community_tokens: Dict[int, str]):
    """Сборка VK-проверки статуса поста через ``wall.getById`` (best-effort).

    Кэширует vk_api-хендл на сообщество. ``post_type``: ``post`` → опубликован,
    ``postpone``/``suggest`` → ещё нет, иначе/пусто → неизвестно.
    """
    import vk_api  # локальный импорт — не тянем в тестах

    sessions: Dict[int, Any] = {}

    def is_published(owner_id: int, post_id: int) -> Optional[bool]:  # pragma: no cover - сеть
        cid = abs(int(owner_id))
        # wall.getById — только user-токеном: community-токен отвечает 27 «method
        # is unavailable with group auth», проверка вечно «неизвестно», и репосты
        # Анны 05.09 ждали дедлайна вместо выхода (инцидент 2026-09-05 11:45).
        token = user_token or community_tokens.get(cid)
        if not token:
            return None
        if cid not in sessions:
            sessions[cid] = vk_api.VkApi(token=token).get_api()
        api = sessions[cid]
        try:
            res = api.wall.getById(posts=f"{owner_id}_{post_id}")
        except Exception as e:
            logger.warning("wall.getById %s_%s failed: %s", owner_id, post_id, e)
            return None
        items = (
            res if isinstance(res, list) else (res.get("items") if isinstance(res, dict) else [])
        )
        if not items:
            return None
        pt = items[0].get("post_type")
        if pt == "post":
            return True
        if pt in ("postpone", "suggest"):
            return False
        return None

    return is_published


# ------------------------------------------------------------ двойник поста
#
# Инцидент 2026-09-05: предложенный пост, поставленный в VK-отложку
# (wall.post post_id=<suggest> publish_date), при выходе получает НОВЫЙ id —
# старый (78293) исчезает, вышедший (78299) с той же подписью и текстом лежит
# на стене. Проверка по старому id вечно «неизвестно». Поэтому для оригиналов
# из предложки ищем «двойника» на стене: подпись автора + время ± окно, либо
# совпадение начала текста.

TWIN_WINDOW = timedelta(minutes=45)
TWIN_TEXT_PREFIX = 40


def _norm_text(text: Optional[str]) -> str:
    return " ".join((text or "").split())[:TWIN_TEXT_PREFIX].casefold()


def pick_twin(
    items: Sequence[Dict[str, Any]],
    *,
    signer_id: Optional[int],
    text: Optional[str],
    publish_date: Optional[datetime],
) -> Optional[int]:
    """Выбрать из записей стены вышедший двойник отложки (чистая логика).

    ``publish_date`` — МСК naive (как в строке), ``items[].date`` — unix UTC.
    Совпадение: ``post_type == post``, время в окне ``TWIN_WINDOW`` и (подпись
    ``signer_id`` совпала **или** первые 40 символов текста равны).
    """
    if publish_date is None:
        return None
    want_ts = calendar.timegm((publish_date - timedelta(hours=3)).timetuple())  # МСК → UTC
    want_text = _norm_text(text)
    best: Optional[int] = None
    for it in items:
        if not isinstance(it, dict) or it.get("post_type", "post") != "post":
            continue
        try:
            ts = int(it.get("date") or 0)
        except (TypeError, ValueError):
            continue
        if abs(ts - want_ts) > TWIN_WINDOW.total_seconds():
            continue
        by_signer = signer_id is not None and it.get("signer_id") == int(signer_id)
        by_text = bool(want_text) and _norm_text(it.get("text")) == want_text
        if by_signer or by_text:
            pid = it.get("id")
            if isinstance(pid, int) and (best is None or pid > best):
                best = pid
    return best


def _build_default_twin_finder(user_token: Optional[str]):
    """``find_twin(owner_id, signer_id, text, publish_date) -> id`` через wall.get."""
    if not user_token:
        return None
    import vk_api  # локальный импорт — не тянем в тестах

    api = vk_api.VkApi(token=user_token).get_api()

    def find_twin(  # pragma: no cover - сеть
        owner_id: int,
        signer_id: Optional[int],
        text: Optional[str],
        publish_date: Optional[datetime],
    ) -> Optional[int]:
        try:
            res = api.wall.get(owner_id=int(owner_id), count=20)
        except Exception as e:
            logger.warning("wall.get %s failed: %s", owner_id, e)
            return None
        items = res.get("items") if isinstance(res, dict) else res
        return pick_twin(items or [], signer_id=signer_id, text=text, publish_date=publish_date)

    return find_twin


async def build_default_twin_finder_from_routing():
    from modules.vk_token_router import load_vk_routing

    user_token, _community_tokens = await load_vk_routing()
    return _build_default_twin_finder(user_token)


async def resolve_publication(
    session,
    row: AdScheduledPost,
    *,
    is_published: Optional[Callable[[int, int], Optional[bool]]],
    find_twin=None,
) -> Optional[bool]:
    """Вышла ли строка отложки: по id, а для предложки — ещё и по двойнику.

    Нашли двойника → ``row.vk_postponed_post_id`` становится настоящим id
    (``record_published`` и репосты берут именно его). Без commit.
    """
    state: Optional[bool] = None
    if is_published is not None and row.vk_postponed_post_id:
        try:
            state = is_published(int(row.community_vk_id), int(row.vk_postponed_post_id))
        except Exception as e:  # pragma: no cover - защита
            logger.warning("is_published failed for %s: %s", row.id, e)
            state = None
    if state is not None or row.kind != "suggested" or find_twin is None:
        return state
    signer_id: Optional[int] = None
    if row.source_ad_request_id:
        ar = await session.get(AdRequest, int(row.source_ad_request_id))
        if ar is not None and ar.author_vk_id:
            signer_id = int(ar.author_vk_id)
    try:
        twin = find_twin(int(row.community_vk_id), signer_id, row.text, row.publish_date)
    except Exception as e:  # pragma: no cover - защита
        logger.warning("find_twin failed for %s: %s", row.id, e)
        return None
    if not twin:
        return None
    logger.info(
        "reconcile: строка %s — отложка %s вышла как %s_%s (двойник по подписи/тексту)",
        row.id,
        row.vk_postponed_post_id,
        row.community_vk_id,
        twin,
    )
    row.vk_postponed_post_id = int(twin)
    return True


async def build_default_checker_from_routing() -> Optional[Callable[[int, int], Optional[bool]]]:
    """Собрать проверку выхода из живых токенов (для диспетчера репостов)."""
    from modules.vk_token_router import load_vk_routing

    user_token, community_tokens = await load_vk_routing()
    if not user_token and not community_tokens:
        return None
    return _build_default_checker(user_token, community_tokens or {})


async def record_published(
    session,
    row: AdScheduledPost,
    *,
    vk_post_id: Optional[int] = None,
    notify: bool = True,
    pinner=None,
) -> AdPublication:
    """Зафиксировать выход строки отложки — единая правда об учёте.

    ``pinner(owner_id, post_id)`` — закреп после выхода для строк с ``pinned``
    (Этап 2, PR 2C); ``None`` — собирается из токенов при необходимости.

    ``vk_post_id`` — id вышедшей записи (по умолчанию ``row.vk_postponed_post_id``;
    диспетчер репостов передаёт id только что созданного репоста). Создаёт
    ``AdPublication``, при ``client_id`` и ненулевой ``price`` — ``AdPayment``
    (``awaiting``), продвигает клиента в воронке, пишет таймлайн, шлёт клиенту
    ВК-уведомление. Commit — на вызывающем.
    """
    post_vk_id = int(vk_post_id) if vk_post_id else row.vk_postponed_post_id
    row.status = "published"
    if post_vk_id and row.vk_postponed_post_id != post_vk_id:
        row.vk_postponed_post_id = post_vk_id
    pub = AdPublication(
        client_id=row.client_id,
        community_vk_id=row.community_vk_id,
        vk_post_id=post_vk_id,
        region_id=row.region_id,
        scheduled_post_id=row.id,
        price=row.price,
        status="published",
        expires_at=row.expires_at,  # срок снятия (С2) переносим на публикацию
    )
    session.add(pub)
    await session.flush()

    # Деньги ждём (owner: «ожидание оплаты»), только если есть клиент и цена.
    awaiting_created = False
    if row.client_id and row.price:
        session.add(
            AdPayment(
                client_id=row.client_id,
                amount=row.price,
                status="awaiting",
                scheduled_post_id=row.id,
                note="авто: пост опубликован VK",
            )
        )
        awaiting_created = True

    if row.client_id:
        client = await session.get(AdClient, row.client_id)
        if client and client.stage in ("detected", "contacted", "scheduled"):
            client.stage = "published"

    if getattr(row, "pinned", False) and pub.vk_post_id:
        from modules.ad_cabinet import pinning

        if pinner is None:
            pinner = await pinning.build_default_pinner(session)
        await pinning.pin_after_publish(session, row, pub, pinner=pinner)

    kind = getattr(row, "kind", None) or "post"
    log_interaction(
        session,
        kind="published",
        client_id=row.client_id,
        scheduled_post_id=row.id,
        publication_id=pub.id,
        summary=(
            f"Репост вышел (сообщество {row.community_vk_id})"
            if kind == "repost"
            else f"Пост опубликован VK (сообщество {row.community_vk_id})"
        ),
        actor="system",
    )
    if notify and row.client_id and pub.vk_post_id:
        from modules.ad_cabinet.vk_bot import notify as vk_notify

        # С первой картинкой поста (Этап 5): заливка в ЛС до 30 с, реконсилер
        # поминутный — одного фото достаточно, ссылка ведёт на весь пост.
        await vk_notify.notify_client(
            session,
            row.client_id,
            "📣 Ваш пост вышел: " f"https://vk.com/wall{row.community_vk_id}_{pub.vk_post_id}",
            photos=list(row.image_names or [])[:1],
        )
        # Пинг по деньгам владельцу (аудит 2026-09-05: ни одного денежного пинга
        # не было): появился awaiting — дедуп на пост.
        if awaiting_created:
            await vk_notify.notify_owner(
                f"💰 Вышел пост №{row.id} клиента №{row.client_id} в {row.community_vk_id} — "
                f"ждёт оплаты {float(row.price):.0f} ₽ (/ad → Кабинеты)",
                dedup_key=f"awaiting:{row.id}",
                dedup_ttl=24 * 3600,
            )
    return pub


# Сторож зависших отложек (аудит 2026-09-05): пост, который VK не подтвердил
# спустя это время после назначенной даты, — не «ещё в отложке», а потеря
# денег молча (AdPayment не создаётся, в сводке должников его нет).
STALL_AFTER = timedelta(hours=2)
STALL_PING_TTL = 12 * 3600


def _default_stall_alert(text: str, dedup_key: str) -> None:  # pragma: no cover - сеть
    from modules.ad_cabinet import owner_ping

    try:
        owner_ping.notify_owner(text, dedup_key=dedup_key, dedup_ttl=STALL_PING_TTL)
    except Exception:
        logger.warning("stall alert failed", exc_info=True)


async def run_reconcile(
    *,
    session_factory: Optional[Callable] = None,
    is_published: Optional[Callable[[int, int], Optional[bool]]] = None,
    now: Optional[datetime] = None,
    stall_alert: Optional[Callable[[str, str], Any]] = None,
    find_twin=None,
) -> Dict[str, Any]:
    """Реконсилировать опубликованные VK отложки → фиксация в CRM. Возвращает счётчики."""
    import asyncio

    if session_factory is None:
        from database.connection import AsyncSessionLocal

        session_factory = AsyncSessionLocal
    now = now or now_msk()
    stall_alert = stall_alert or (
        lambda text, key: asyncio.get_running_loop().run_in_executor(
            None, _default_stall_alert, text, key
        )
    )

    # Дефолтная VK-проверка собирается лениво (нужны токены) — только если не инжектирована.
    auto_checker = is_published is None
    if is_published is None:
        is_published = await build_default_checker_from_routing()
        if is_published is None:
            logger.warning("reconcile: нет VK-токенов, пропуск")
            return {"reconciled": 0, "checked": 0, "skipped": "no_token"}
    # Поиск двойника строится из роутинга только вместе с автоматической
    # проверкой: инжектированная проверка (тесты, пробы) = вызывающий сам
    # решает, есть ли у него ВК; сеть не трогаем.
    if find_twin is None and auto_checker:
        find_twin = await build_default_twin_finder_from_routing()

    reconciled = 0
    stalled = 0
    async with session_factory() as session:
        rows = (
            (
                await session.execute(
                    select(AdScheduledPost).where(
                        AdScheduledPost.status == "scheduled",
                        AdScheduledPost.vk_postponed_post_id.isnot(None),
                        AdScheduledPost.publish_date <= now,
                        # Репосты фиксирует диспетчер планировщика предложки.
                        AdScheduledPost.kind != "repost",
                    )
                    # Гонка «реконсилер ↔ отмена ↔ второй прогон» (аудит 2026-09-05):
                    # строка блокируется до коммита, конкурент её пропускает.
                    # На sqlite (тесты) — no-op.
                    .with_for_update(skip_locked=True)
                )
            )
            .scalars()
            .all()
        )
        for row in rows:
            try:
                state = await resolve_publication(
                    session, row, is_published=is_published, find_twin=find_twin
                )
            except Exception as e:  # pragma: no cover - защита
                logger.warning("reconcile check failed for post %s: %s", row.id, e)
                state = None
            if state is not True:
                # Зависла? Через STALL_AFTER после даты — владельцу пинг (дедуп
                # 12 ч на строку), в строке — пометка. Статус не меняем: VK может
                # подтвердить позже, и тогда фиксация пройдёт штатно.
                if row.publish_date and now - row.publish_date >= STALL_AFTER:
                    hours = int((now - row.publish_date).total_seconds() // 3600)
                    row.error_message = f"VK не подтвердил выход за {hours} ч (post_type={state})"
                    stalled += 1
                    res = stall_alert(
                        f"⏳ Отложка №{row.id} в {row.community_vk_id} не подтверждена VK "
                        f"{hours} ч после {row.publish_date:%d.%m %H:%M} — проверь стену, "
                        "счёт клиенту не выставлен.",
                        f"stalled:{row.id}",
                    )
                    if hasattr(res, "__await__"):
                        await res
                continue
            await record_published(session, row)
            reconciled += 1

        await session.commit()

    logger.info("reconcile: checked=%d, reconciled=%d, stalled=%d", len(rows), reconciled, stalled)
    return {"reconciled": reconciled, "checked": len(rows), "stalled": stalled}
