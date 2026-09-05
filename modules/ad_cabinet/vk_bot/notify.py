"""Исходящие уведомления бота: кабинет → личка ВК клиента и владельца.

Канал — ``messages.send`` от имени сообщества «САРАФАН» community-токеном
(тот же путь, что у Радиоточки: ``tasks/radar_tasks._run_vk_intake.reply``).
Пользователю сообщество может писать только если он разрешил ему сообщения
(ошибки VK 900/901/902) — это не сбой, а «клиент не подключил ВК-канал»,
и пишется в лог на DEBUG.

Кому писать за клиента: ``radar_users.vk_user_id`` привязанного аккаунта, а
для клиентов из предложки без аккаунта — ``ad_clients.author_vk_id``, если
это человек (положительный id; сообществу в личку не пишут).

Владельцу — зеркало Telegram-пинга: :func:`notify_owner` шлёт и в Telegram
(через ``owner_ping``, с его же дедупом), и в ВК на ``SETKA_OWNER_VK_IDS``.
Один вызов — оба канала; если один упал, второй всё равно уходит.

Сетевой вызов инъектируется (``sender``) — тесты гоняют логику без ВК.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional, Sequence, Tuple

import httpx

from database.models import AdClient
from database.models_extended import RadarUser
from modules.ad_cabinet import client_photos, vk_photo_upload
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

VK_API = "https://api.vk.com/method/{method}"
VK_V = "5.199"
SEND_TIMEOUT = 10  # секунд на ВК; висящий ВК не должен держать запрос клиента

#: Коды «сообщество не может писать этому пользователю» — не ошибка бота.
NOT_ALLOWED_CODES = (900, 901, 902)

#: ``kind`` записи журнала об отправленном уведомлении.
KIND_NOTICE = "vk_notice"

#: ``sender(peer_id, text, keyboard=None, attachment=None)`` → ответ ВК. Четвёртый
#: аргумент передаётся ТОЛЬКО когда вложение есть — трёхаргументные двойники
#: (тесты, старые вызовы) продолжают работать.
Sender = Callable[..., Awaitable[Dict[str, Any]]]


# ---------------------------------------------------------------- конфиг


async def community() -> Optional[Tuple[int, str]]:
    """``(group_id, community_token)`` САРАФАНа или ``None`` — бот выключен.

    Выключен, пока владелец не задал ``SARAFAN_VK_COMMUNITY_ID`` и не положил
    community-токен этого сообщества в ``/tokens``.
    """
    from config.runtime import get_sarafan_vk_community_id

    group_id = get_sarafan_vk_community_id()
    if not group_id:
        return None
    from modules.vk_token_router import load_community_routing

    # Боту нужен только community-токен: мёртвые user-токены его не выключают.
    community_tokens = await load_community_routing()
    token = (community_tokens or {}).get(group_id)
    if not token:
        logger.debug("vk_bot: community %s has no token in /tokens — off", group_id)
        return None
    return group_id, token


def owner_vk_ids() -> Tuple[int, ...]:
    """Аккаунты владельца в ВК (``SETKA_OWNER_VK_IDS``, дефолт — Valstan)."""
    from middleware.auth_gate import OWNER_VK_IDS_DEFAULT, _csv_env

    out = []
    for raw in _csv_env("SETKA_OWNER_VK_IDS", OWNER_VK_IDS_DEFAULT):
        try:
            out.append(int(raw))
        except (TypeError, ValueError):
            continue
    return tuple(sorted(out))


# ---------------------------------------------------------------- отправка


async def vk_send(
    token: str,
    group_id: int,
    peer_id: int,
    text: str,
    keyboard: Optional[str] = None,
    attachment: Optional[str] = None,
) -> Dict[str, Any]:
    """``messages.send`` от имени сообщества. Возвращает ответ ВК (``{}`` при сети)."""
    params: Dict[str, Any] = {
        "peer_id": int(peer_id),
        "message": text,
        "random_id": random.randint(1, 2**31 - 1),
        "group_id": int(group_id),
        "access_token": token,
        "v": VK_V,
    }
    if keyboard:
        params["keyboard"] = keyboard
    if attachment:
        params["attachment"] = attachment
    # Пауза DM-канала после 9/14 и общий лимитер/учёт по токену (Этап 3).
    from modules.ad_cabinet import dm_channel

    until = await asyncio.to_thread(dm_channel.paused_until, int(group_id))
    if until is not None:
        return {
            "error": {"error_code": 9, "error_msg": f"канал на паузе до {until:%d.%m %H:%M} UTC"}
        }
    try:
        from modules.vk_monitor.vk_client import enforce_token_rate_limit

        await asyncio.to_thread(enforce_token_rate_limit, token, "messages.send")
    except Exception:  # noqa: BLE001 - учёт не роняет отправку
        logger.debug("vk_bot throttle hook failed")
    try:
        async with httpx.AsyncClient(timeout=SEND_TIMEOUT) as client:
            r = await client.post(VK_API.format(method="messages.send"), data=params)
            data = r.json()
    except Exception as e:  # noqa: BLE001 - сеть; уведомление не роняет действие
        logger.warning("vk_bot messages.send failed: %s", e)
        return {}
    err = (data or {}).get("error") if isinstance(data, dict) else None
    if isinstance(err, dict) and err.get("error_code") in dm_channel.PAUSE_CODES:
        await asyncio.to_thread(dm_channel.note_error, int(group_id), int(err["error_code"]))
    return data


def _make_sender(token: str, group_id: int) -> Sender:
    async def _send(
        peer_id: int,
        text: str,
        keyboard: Optional[str] = None,
        attachment: Optional[str] = None,
    ):
        return await vk_send(
            token, group_id, peer_id, text, keyboard=keyboard, attachment=attachment
        )

    return _send


#: Attachment-строки заливок в ЛС: ``(client_id, имена, peer_id) → (когда, строка)``.
#: Заказ на 30 районов даёт 30 «Ваш пост вышел» с одним фото в один тик
#: реконсилера — заливаем один раз, строка после saveMessagesPhoto переиспользуема.
_ATTACHMENT_CACHE: Dict[Tuple[int, Tuple[str, ...], Optional[int]], Tuple[float, str]] = {}
ATTACHMENT_CACHE_TTL = 600.0


async def upload_client_photos(
    token: Optional[str],
    client_id: int,
    names: Sequence[str],
    *,
    peer_id: Optional[int] = None,
    group_id: Optional[int] = None,
) -> Optional[str]:
    """Залить фото клиента из его библиотеки в ЛС → attachment-строка или ``None``.

    Грузим community-токеном отправляющего сообщества (требование
    ``vk_photo_upload``), не больше ``MAX_MESSAGE_PHOTOS``; пропавшие файлы
    пропускаем. Канал сообщества на паузе (9/14) — не заливаем вовсе (текст всё
    равно не уйдёт); заливка идёт через общий лимитер токена и кэшируется на
    ``ATTACHMENT_CACHE_TTL``. Любая ошибка → ``None``: картинка никогда не
    блокирует текст.
    """
    if not token or not names:
        return None
    try:
        from modules.ad_cabinet import dm_channel

        if group_id and await asyncio.to_thread(dm_channel.paused_until, int(group_id)):
            return None
        wanted = {Path(str(n)).name for n in names if n}
        paths = [p for p in client_photos.client_photo_paths(client_id) if p.name in wanted]
        paths = paths[: vk_photo_upload.MAX_MESSAGE_PHOTOS]
        if not paths:
            return None
        key = (int(client_id), tuple(p.name for p in paths), int(peer_id) if peer_id else None)
        now = time.monotonic()
        hit = _ATTACHMENT_CACHE.get(key)
        if hit and now - hit[0] < ATTACHMENT_CACHE_TTL:
            return hit[1]
        try:
            from modules.vk_monitor.vk_client import enforce_token_rate_limit

            await asyncio.to_thread(enforce_token_rate_limit, token, "photos.saveMessagesPhoto")
        except Exception:  # noqa: BLE001 - учёт не роняет заливку
            logger.debug("vk_bot upload throttle hook failed")
        import vk_api

        api = vk_api.VkApi(token=token).get_api()
        images = [p.read_bytes() for p in paths]
        att = await asyncio.to_thread(
            vk_photo_upload.upload_offer_images, api, images, peer_id=peer_id
        )
        if att:
            if len(_ATTACHMENT_CACHE) > 200:
                _ATTACHMENT_CACHE.clear()
            _ATTACHMENT_CACHE[key] = (now, att)
        return att or None
    except Exception:  # noqa: BLE001 - фото не блокирует текст
        logger.warning("vk_bot upload_client_photos failed (client %s)", client_id, exc_info=True)
        return None


def send_outcome(resp: Dict[str, Any]) -> Tuple[bool, Optional[int]]:
    """``(отправлено, код ошибки ВК | None)`` из ответа ``messages.send``. Чистая."""
    if not isinstance(resp, dict):
        return False, None
    if "error" in resp:
        err = resp.get("error") or {}
        try:
            return False, int(err.get("error_code"))
        except (TypeError, ValueError):
            return False, None
    return "response" in resp, None


# ---------------------------------------------------------------- клиенту


def client_peer_id(client: AdClient, user: Optional[RadarUser]) -> Optional[int]:
    """Кому писать за клиента: аккаунт ЕСА с ВК-входом, иначе автор из предложки.

    Сообществам (отрицательный id) в личку не пишут → ``None``. Чистая.
    """
    if user is not None and user.vk_user_id:
        return int(user.vk_user_id)
    if client.author_vk_id and int(client.author_vk_id) > 0 and not client.author_is_group:
        return int(client.author_vk_id)
    return None


async def notify_client(
    session,
    client_id: int,
    text: str,
    *,
    keyboard: Optional[str] = None,
    sender: Optional[Sender] = None,
    log: bool = True,
    attachment: Optional[str] = None,
    photos: Optional[Sequence[str]] = None,
) -> bool:
    """Написать клиенту в личку ВК от имени САРАФАНа. ``True`` — доставлено.

    Молчит (``False``) если бот выключен, у клиента нет ВК-адресата или он не
    разрешил сообщения сообществу. Успешная отправка пишется в журнал кабинета
    (``vk_notice``, actor=system), чтобы таймлайн показывал, что клиент
    получил. Без commit — коммитит вызывающий.

    ``attachment`` — готовая attachment-строка ВК; ``photos`` — имена файлов из
    библиотеки клиента, их зальём community-токеном (Этап 5). Не залилось —
    уходит текст без картинки.
    """
    try:
        client = await session.get(AdClient, client_id)
        if client is None:
            return False
        user = await session.get(RadarUser, client.radar_user_id) if client.radar_user_id else None
        peer_id = client_peer_id(client, user)
        if peer_id is None:
            return False

        token: Optional[str] = None
        group_id: Optional[int] = None
        if sender is None:
            conf = await community()
            if conf is None:
                return False
            sender = _make_sender(conf[1], conf[0])
            group_id, token = int(conf[0]), conf[1]
        if photos and attachment is None:
            attachment = await upload_client_photos(
                token, client_id, photos, peer_id=peer_id, group_id=group_id
            )

        resp = (
            await sender(peer_id, text, keyboard, attachment)
            if attachment
            else await sender(peer_id, text, keyboard)
        )
        ok, code = send_outcome(resp)
        if not ok:
            level = logging.DEBUG if code in NOT_ALLOWED_CODES else logging.WARNING
            logger.log(level, "vk_bot: client %s not notified (vk error %s)", client_id, code)
            return False
        if log:
            log_interaction(
                session,
                kind=KIND_NOTICE,
                client_id=client_id,
                summary=f"ВК-уведомление клиенту: {text[:120]}"
                + (" (с фото)" if attachment else ""),
                actor="system",
            )
        return True
    except Exception:  # noqa: BLE001 - уведомление не роняет действие
        logger.warning("vk_bot notify_client failed", exc_info=True)
        return False


# ---------------------------------------------------------------- владельцу


async def notify_owner(
    text: str,
    *,
    dedup_key: Optional[str] = None,
    dedup_ttl: int = 3600,
    sender: Optional[Sender] = None,
    telegram: bool = True,
) -> Dict[str, bool]:
    """Пинг владельцу в оба канала: Telegram (``owner_ping``) и личка ВК.

    Дедуп — один на оба канала (ключ ``owner_ping``): если Telegram-пинг по
    этому ключу уже уходил в ``dedup_ttl``, ВК тоже молчит. Возвращает
    ``{"telegram": bool, "vk": bool}``; ошибки глотаются.
    """
    from modules.ad_cabinet import owner_ping

    out = {"telegram": False, "vk": False}
    try:
        if dedup_key and not await asyncio.to_thread(
            owner_ping.ping_dedup_pass, dedup_key, ttl=dedup_ttl
        ):
            return out
        if telegram:
            out["telegram"] = await asyncio.to_thread(owner_ping.notify_owner, text)

        if sender is None:
            conf = await community()
            if conf is None:
                return out
            sender = _make_sender(conf[1], conf[0])
        sent_any = False
        for vk_id in owner_vk_ids():
            ok, code = send_outcome(await sender(vk_id, text, None))
            if ok:
                sent_any = True
            elif code in NOT_ALLOWED_CODES:
                logger.info(
                    "vk_bot: владелец %s не разрешил сообщения сообществу — ВК-пинг молчит", vk_id
                )
        out["vk"] = sent_any
    except Exception:  # noqa: BLE001
        logger.warning("vk_bot notify_owner failed", exc_info=True)
    finally:
        # Ключ съеден ДО отправки; если ни один канал не доставил — вернуть его,
        # иначе следующее честное событие того же ключа молчит весь ttl
        # (аудит 2026-09-05).
        if dedup_key and not out["telegram"] and not out["vk"]:
            try:
                await asyncio.to_thread(owner_ping.release_dedup, dedup_key)
            except Exception:  # noqa: BLE001
                logger.debug("release_dedup failed for %s", dedup_key)
    return out


__all__ = [
    "community",
    "owner_vk_ids",
    "vk_send",
    "upload_client_photos",
    "send_outcome",
    "client_peer_id",
    "notify_client",
    "notify_owner",
    "KIND_NOTICE",
    "NOT_ALLOWED_CODES",
]
