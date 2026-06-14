"""Доставка найденного в целевые каналы вывода радара (миграция 045, кабинет).

Хук вызывается поллером ПОСЛЕ коммита новых элементов (рядом с web-push), best-
effort: сбой доставки не валит прогон. Для каждого активного вывода типа
``telegram``/``vk`` берём новые элементы юзера (id > курсора last_item_id) из его
АКТИВНЫХ подписок и шлём по режиму (``excerpt_link`` начало+ссылка / ``full``
целиком).

Гарантии:
- **at-most-once** по монотонному ``radar_items.id``: курсор двигается за
  обработанными элементами независимо от исхода отдельной отправки — один битый
  элемент не клинит поток (как web-push; для ленты уведомлений это приемлемо);
- **bounded**: не более ``DELIVERY_BATCH`` элементов за прогон на вывод — нет
  бэклог-флуда; хвост уедет следующими прогонами (поллер раз в 10 мин);
- **throttle**: пауза между реальными отправками (анти-флуд);
- **per-output изоляция**: сбой одного вывода (или одного юзера) не валит другие;
- **видимость #018**: ``fail_count``/``last_error`` на выводе — кабинет покажет
  «вывод молчит/ошибка», отличая «нет нового» от «доставка сломалась».

``feed``-вывод (внутренняя лента) — no-op: лента наполняется поллером сама, эта
запись существует для полноты списка выводов в кабинете.

Probe-факт (этот бокс, 2026-06-14): api.telegram.org доступен — TG-вывод текстом
идёт напрямую через Bot API, relay не нужен. VK ``wall.post`` — VKPublisher под
токен-полиси (как рассылка/krugozor). Медиа в VK не рехостим (атрибуция текстом —
урок G64); в TG медиа уезжает в режиме ``full``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

# Сколько элементов максимум доставить за один прогон на один вывод (анти-флуд).
DELIVERY_BATCH = 10
# Пауза между реальными отправками в один внешний канал (сек).
DELIVERY_THROTTLE_SECONDS = 1.0
# Длина «начала» в режиме excerpt_link.
EXCERPT_CHARS = 500

EXTERNAL_TYPES = ("telegram", "vk")


def format_item(item: Dict[str, Any], mode: str, *, source_title: Optional[str] = None) -> str:
    """Текст элемента под режим вывода.

    excerpt_link — начало (до EXCERPT_CHARS) + ссылка на оригинал; full — целиком
    + ссылка. Атрибуция источником строкой сверху (если есть). Ссылку добавляем
    всегда, когда она известна — это «начало+ссылка» владельца и атрибуция G64.
    """
    title = (item.get("title") or "").strip()
    body = (item.get("text") or "").strip()
    if not body and title:
        body, title = title, ""

    if mode != "full" and len(body) > EXCERPT_CHARS:
        body = body[:EXCERPT_CHARS].rstrip() + "…"

    parts: List[str] = []
    head = source_title or item.get("source_title")
    if head:
        parts.append(f"📡 {head}".strip())
    if title and title != body:
        parts.append(title)
    if body:
        parts.append(body)
    url = (item.get("url") or "").strip()
    if url:
        parts.append(f"🔗 {url}")
    return "\n\n".join(p for p in parts if p).strip()


def _item_media(item: Dict[str, Any]):
    """RadarItem.media [{type,url}] → ResolvedMedia (фото + прямые .mp4-видео)."""
    from modules.publisher.telegram_repost import ResolvedMedia, _is_sendable_video_url

    media = ResolvedMedia()
    for m in item.get("media") or []:
        if not isinstance(m, dict):
            continue
        url = m.get("url")
        if not url:
            continue
        if m.get("type") == "photo":
            media.photos.append(url)
        elif m.get("type") == "video" and _is_sendable_video_url(url):
            media.videos.append(url)
    return media


# ───────────────────────── адаптеры по умолчанию ─────────────────────────


async def _default_tg_sender(output, item: Dict[str, Any], text: str) -> bool:
    """TG-вывод: бот sendMessage в target (chat_id/@channel). Медиа — в full."""
    from modules.publisher.telegram_repost import ResolvedMedia, repost_to_telegram

    bot_name = ((output.config or {}).get("bot_name") or "").strip()
    if not bot_name:
        from config.runtime import get_radar_bot_name

        bot_name = get_radar_bot_name() or ""
    if not bot_name:
        logger.warning("radar delivery: no tg bot configured for output %s", output.id)
        return False

    media = _item_media(item) if output.mode == "full" else ResolvedMedia()
    res = await repost_to_telegram(bot_name, str(output.target or "").strip(), text, media)
    return bool(res.get("success"))


def _make_default_vk_sender(session) -> Callable[[Any, Dict[str, Any], str], Awaitable[bool]]:
    """VK-вывод: один publisher на прогон под токен-полиси (как рассылка)."""
    publisher_box: Dict[str, Any] = {}

    async def _send(output, item: Dict[str, Any], text: str) -> bool:
        from modules.publisher.vk_publisher_extended import VKPublisher

        if "p" not in publisher_box:
            publisher_box["p"] = await VKPublisher.create_with_policy(session, target_group_id=None)
        try:
            owner_id = int(str(output.target).strip())
        except (TypeError, ValueError):
            logger.warning(
                "radar delivery: bad vk target %r on output %s", output.target, output.id
            )
            return False
        res = await publisher_box["p"].publish_digest(group_id=owner_id, text=text)
        return bool(res.get("success") or res.get("post_id"))

    return _send


async def _deliver_one(output, item: Dict[str, Any], *, tg_sender, vk_sender) -> bool:
    """Отправить один элемент в один вывод по его типу/режиму. Не бросает."""
    text = format_item(item, output.mode, source_title=item.get("source_title"))
    if not text:
        return True  # пустой элемент — нечего слать, не ошибка
    try:
        if output.type == "telegram":
            return await tg_sender(output, item, text)
        if output.type == "vk":
            return await vk_sender(output, item, text)
    except Exception as e:  # noqa: BLE001 — per-output изоляция
        logger.warning("radar delivery send failed (output %s): %s", output.id, e)
        return False
    return False


async def _new_items_for_output(session, output) -> List[Dict[str, Any]]:
    """Новые элементы юзера (id > курсор) из его АКТИВНЫХ подписок, ASC, bounded."""
    from database.models_extended import RadarItem, RadarSource, RadarSubscription

    source_ids = select(RadarSubscription.source_id).where(
        RadarSubscription.user_id == output.user_id,
        RadarSubscription.is_active.is_(True),
    )
    rows = (
        await session.execute(
            select(RadarItem, RadarSource.title)
            .join(RadarSource, RadarSource.id == RadarItem.source_id)
            .where(
                RadarItem.source_id.in_(source_ids),
                RadarItem.id > (output.last_item_id or 0),
            )
            .order_by(RadarItem.id.asc())
            .limit(DELIVERY_BATCH)
        )
    ).all()
    out: List[Dict[str, Any]] = []
    for item, source_title in rows:
        payload = item.to_dict()
        payload["source_title"] = source_title
        out.append(payload)
    return out


async def deliver_new_items(
    *,
    session_factory: Optional[Callable] = None,
    tg_sender: Optional[Callable] = None,
    vk_sender: Optional[Callable] = None,
    throttle: float = DELIVERY_THROTTLE_SECONDS,
) -> Dict[str, Any]:
    """Прогон доставки: разослать новые элементы по всем активным внешним выводам.

    Возвращает сводку {outputs, delivered, failed}. Senders инжектируемы (тесты).
    """
    from database import models  # noqa: F401 - конфигурация мапперов (PR #189)
    from database.connection import AsyncSessionLocal
    from database.models_extended import RadarOutput

    if session_factory is None:
        session_factory = AsyncSessionLocal

    summary = {"outputs": 0, "delivered": 0, "failed": 0}
    async with session_factory() as session:
        outputs = (
            (
                await session.execute(
                    select(RadarOutput).where(
                        RadarOutput.is_active.is_(True),
                        RadarOutput.type.in_(EXTERNAL_TYPES),
                    )
                )
            )
            .scalars()
            .all()
        )
        if not outputs:
            return summary

        if tg_sender is None:
            tg_sender = _default_tg_sender
        if vk_sender is None:
            vk_sender = _make_default_vk_sender(session)

        for output in outputs:
            items = await _new_items_for_output(session, output)
            if not items:
                continue
            summary["outputs"] += 1
            had_error: Optional[str] = None
            sent_any = False
            for item in items:
                if sent_any and throttle > 0:
                    await asyncio.sleep(throttle)
                ok = await _deliver_one(output, item, tg_sender=tg_sender, vk_sender=vk_sender)
                sent_any = True
                if ok:
                    summary["delivered"] += 1
                else:
                    summary["failed"] += 1
                    had_error = f"item {item['id']}: доставка не удалась"
            # Курсор двигаем за всеми обработанными (at-most-once): один битый
            # элемент не клинит поток. Ошибку фиксируем для видимости кабинета.
            output.last_item_id = items[-1]["id"]
            output.last_delivery_at = datetime.utcnow()
            if had_error:
                output.fail_count = (output.fail_count or 0) + 1
                output.last_error = had_error[:512]
            else:
                output.fail_count = 0
                output.last_error = None
        await session.commit()

    if summary["delivered"] or summary["failed"]:
        logger.info("radar delivery: %s", summary)
    return summary


async def send_test_output(
    *,
    output_id: int,
    user_id: int,
    session_factory: Optional[Callable] = None,
    tg_sender: Optional[Callable] = None,
    vk_sender: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Кнопка «тест вывода»: отправить синтетический элемент в канал.

    Валидирует TG/VK-настройку юзера до того, как он на неё положится (probe на
    уровне пользователя). Курсор НЕ трогаем — тест вне доставки. Возвращает
    {ok: bool, detail: str}.
    """
    from database import models  # noqa: F401
    from database.connection import AsyncSessionLocal
    from database.models_extended import RadarOutput

    if session_factory is None:
        session_factory = AsyncSessionLocal

    sample = {
        "id": 0,
        "title": "Проверка вывода радара",
        "text": (
            "Это тестовое сообщение из вашего радара SARAFAN. "
            "Если вы его видите — доставка в этот канал работает."
        ),
        "url": "https://vk.com/sarafan",
        "media": [],
        "source_title": "Радар · тест",
    }

    async with session_factory() as session:
        output = (
            await session.execute(
                select(RadarOutput).where(
                    RadarOutput.id == output_id, RadarOutput.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if output is None:
            return {"ok": False, "detail": "Вывод не найден"}
        if output.type == "feed":
            return {"ok": True, "detail": "Лента радара — внутренний вывод, всегда доступен"}
        if tg_sender is None:
            tg_sender = _default_tg_sender
        if vk_sender is None:
            vk_sender = _make_default_vk_sender(session)
        ok = await _deliver_one(output, sample, tg_sender=tg_sender, vk_sender=vk_sender)
        # Тест тоже отражаем в здоровье вывода — чтобы кабинет не врал.
        if ok:
            output.fail_count = 0
            output.last_error = None
        else:
            output.last_error = "тест доставки не удался"
        await session.commit()
    return {
        "ok": ok,
        "detail": (
            "Отправлено — проверьте канал" if ok else "Не удалось отправить (проверьте настройку)"
        ),
    }


async def max_item_id(session) -> int:
    """Текущий MAX(radar_items.id) — старт курсора нового вывода (без бэклога)."""
    from database.models_extended import RadarItem

    val = (await session.execute(select(func.coalesce(func.max(RadarItem.id), 0)))).scalar()
    return int(val or 0)
