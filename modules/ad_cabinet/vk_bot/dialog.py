"""Диалог бота в личке САРАФАНа: меню-клавиатура и машина состояний (этап 2).

Клиент нажимает кнопки, бот отвечает текстом и той же клавиатурой. Всё, что
бот умеет, — те же функции, что у кабинета: баланс (``compute_balance`` +
пакеты), прайс (``config.ad_landing``), заказ (``client_orders.submit_order``,
цену считает только сервер), чат (``chat.post_message``). Ничего нового про
деньги и посты здесь не решается — только разговор.

Состояние диалога — маленький словарь ``{"step": ..., "draft": {...}}``, хранит
его вызывающий (в проде — Redis, ключ на ``peer_id``; в тестах — dict). Бот
получает состояние и возвращает новое; ``None`` — диалог сброшен в меню.

Кто клиент: карточка ``ad_clients`` по ВК-id — через аккаунт ЕСА с ВК-входом,
через ``author_vk_id`` (клиенты из предложки), а если никого нет — **карточка
заводится автоматически** (решение владельца 02.09: «цеплять всех, лишних
потом удалю»). Новая карточка — ``trusted=False``, первые посты идут через
одобрение владельца, как и из кабинета.

Ответы — список ``(text, keyboard_json | None)``; ВК режет сообщение на 4096
символов, длинные списки районов разбиваются заранее.

Фото (Этап 5): на шагах заказа вложения ``photo`` из сообщения качаются
инъекцией ``photo_fetch`` (сеть) и кладутся в библиотеку клиента
(``client_photos``, тот же каталог, что у кабинета); в состоянии хранятся только
имена файлов (``draft["photos"]``, JSON для Redis) — они и уходят в
``submit_order(image_paths=...)``. Без ``photo_fetch`` вложения игнорируются.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Sequence, Tuple

from sqlalchemy import select

from database.models import AdClient, AdPayment, AdPublication, AdScheduledPost, Region
from database.models_extended import RadarUser
from modules.ad_cabinet import chat, client_orders, client_photos, photo_retention
from modules.ad_cabinet.balance import compute_balance
from modules.ad_cabinet.interaction_log import log_interaction

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------- команды

CMD_BALANCE = "balance"
CMD_PRICES = "prices"
CMD_ORDER = "order"
CMD_PAY = "pay"
CMD_PAID = "paid"  # «Я оплатил» — заявить оплату ожидающих счетов (PR 1.7)
CMD_CHAT = "chat"
CMD_CABINET = "cabinet"
CMD_CANCEL = "cancel"
CMD_NOW = "now"
CMD_ALL_REGIONS = "all_regions"
CMD_CONFIRM = "confirm"
CMD_REGION = "rg"  # payload {"cmd":"rg","id":<region_id>} — переключить район
CMD_REGION_PAGE = "rgpage"  # payload {"cmd":"rgpage","p":<n>} — страница списка
CMD_REGIONS_DONE = "rgdone"  # выбор районов закончен

#: Текст кнопки → команда. Нужен, потому что ВК шлёт ``payload`` не всегда
#: (старые клиенты, ручной ввод) — текст кнопки распознаём тоже.
BUTTON_TEXT: Dict[str, str] = {
    "💰 Баланс": CMD_BALANCE,
    "📋 Цены": CMD_PRICES,
    "🛒 Заказать пост": CMD_ORDER,
    "💳 Оплата": CMD_PAY,
    "✅ Оплатил": CMD_PAID,
    "💬 Написать": CMD_CHAT,
    "🏠 Кабинет": CMD_CABINET,
    "❌ Отмена": CMD_CANCEL,
    "⚡ Сейчас": CMD_NOW,
    "🌐 Все районы": CMD_ALL_REGIONS,
    "✅ Подтвердить": CMD_CONFIRM,
    "✅ Готово": CMD_REGIONS_DONE,
}

CABINET_URL = "https://сарафан.вмалмыже.рф/cabinet"

STEP_ORDER_TEXT = "order_text"
STEP_ORDER_REGIONS = "order_regions"
STEP_ORDER_WHEN = "order_when"
STEP_ORDER_CONFIRM = "order_confirm"
STEP_CHAT = "chat"

VK_MSG_MAX = 4000  # запас до лимита 4096


def _btn(label: str, cmd: str, color: str = "secondary") -> Dict[str, Any]:
    return {
        "action": {"type": "text", "label": label, "payload": json.dumps({"cmd": cmd})},
        "color": color,
    }


def keyboard(rows: Sequence[Sequence[Dict[str, Any]]], *, one_time: bool = False) -> str:
    return json.dumps(
        {"one_time": one_time, "buttons": [list(r) for r in rows]}, ensure_ascii=False
    )


MAIN_KEYBOARD = keyboard(
    [
        [_btn("💰 Баланс", CMD_BALANCE, "primary"), _btn("📋 Цены", CMD_PRICES)],
        [_btn("🛒 Заказать пост", CMD_ORDER, "positive")],
        [_btn("💳 Оплата", CMD_PAY), _btn("✅ Оплатил", CMD_PAID, "positive")],
        [_btn("💬 Написать", CMD_CHAT), _btn("🏠 Кабинет", CMD_CABINET)],
    ]
)
CANCEL_KEYBOARD = keyboard([[_btn("❌ Отмена", CMD_CANCEL, "negative")]])
WHEN_KEYBOARD = keyboard(
    [[_btn("⚡ Сейчас", CMD_NOW, "primary")], [_btn("❌ Отмена", CMD_CANCEL, "negative")]]
)
CONFIRM_KEYBOARD = keyboard(
    [[_btn("✅ Подтвердить", CMD_CONFIRM, "positive"), _btn("❌ Отмена", CMD_CANCEL, "negative")]]
)
#: На шаге текста: «Готово» без текста = пост только из фото (та же подпись и
#: команда, что у «Готово» районов — BUTTON_TEXT не меняется).
TEXT_DONE_KEYBOARD = keyboard(
    [[_btn("✅ Готово", CMD_REGIONS_DONE, "positive"), _btn("❌ Отмена", CMD_CANCEL, "negative")]]
)
ORDER_STEPS = (STEP_ORDER_TEXT, STEP_ORDER_REGIONS, STEP_ORDER_WHEN, STEP_ORDER_CONFIRM)


# ---------------------------------------------------------------- модель


Reply = Tuple[str, Optional[str]]

#: ``submit(session, client, draft) -> результат submit_order``. Инъекция:
#: в проде — реальная отправка в VK-отложку (для trusted), в тестах — двойник.
Submitter = Callable[[Any, AdClient, Dict[str, Any]], Awaitable[Dict[str, Any]]]
#: ``name_fetch(vk_id) -> "Имя Фамилия"`` для новой карточки. ``None`` — без имени.
NameFetch = Callable[[int], Awaitable[Optional[str]]]
#: ``photo_fetch(url) -> bytes | None`` — скачать фото из вложения по ссылке из
#: ``photo.sizes``. Единственная сетевая инъекция диалога; ``None`` — не удалось.
PhotoFetch = Callable[[str], Awaitable[Optional[bytes]]]


@dataclass
class Incoming:
    peer_id: int
    text: str = ""
    payload: Optional[Dict[str, Any]] = None
    attachments: List[Dict[str, Any]] = field(default_factory=list)

    def command(self) -> Optional[str]:
        """Команда из payload кнопки либо по её тексту."""
        if isinstance(self.payload, dict) and self.payload.get("cmd"):
            return str(self.payload["cmd"])
        t = (self.text or "").strip()
        if t in BUTTON_TEXT:
            return BUTTON_TEXT[t]
        low = t.lower()
        if low in ("отмена", "меню", "/start", "start", "начать"):
            return CMD_CANCEL
        return None


def parse_payload(raw: Any) -> Optional[Dict[str, Any]]:
    """``payload`` из события ВК — строка JSON либо уже dict. Чистая."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            v = json.loads(raw)
            return v if isinstance(v, dict) else None
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- клиент


async def find_client(session, vk_id: int) -> Optional[AdClient]:
    """Карточка по ВК-id: аккаунт ЕСА с ВК-входом → автор предложки."""
    row = (
        await session.execute(
            select(AdClient)
            .join(RadarUser, RadarUser.id == AdClient.radar_user_id)
            .where(RadarUser.vk_user_id == int(vk_id))
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    return (
        (
            await session.execute(
                select(AdClient)
                .where(AdClient.author_vk_id == int(vk_id))
                .order_by(AdClient.id.asc())
            )
        )
        .scalars()
        .first()
    )


async def ensure_client(
    session, vk_id: int, *, name_fetch: Optional[NameFetch] = None
) -> Tuple[AdClient, bool]:
    """Карточка для ВК-собеседника; нет — завести. ``(client, created)``.

    Без commit — коммитит вызывающий. Новая карточка помечается в журнале как
    ``cabinet_signup`` с пометкой «из ВК-бота», чтобы в ленте активности было
    видно, откуда пришёл клиент.
    """
    row = await find_client(session, vk_id)
    if row is not None:
        return row, False
    name = None
    if name_fetch is not None:
        try:
            name = await name_fetch(int(vk_id))
        except Exception:  # noqa: BLE001 - имя не критично
            name = None
    row = AdClient(
        author_vk_id=int(vk_id),
        author_is_group=False,
        name=(name or "").strip() or None,
        stage="detected",
        trusted=False,
    )
    session.add(row)
    await session.flush()
    log_interaction(
        session,
        kind="cabinet_signup",
        client_id=row.id,
        summary=f"Новый клиент из ВК-бота: {row.name or 'vk id ' + str(vk_id)}",
        actor="client",
        meta={"source": "vk_bot", "vk_id": int(vk_id)},
    )
    return row, True


# ---------------------------------------------------------------- тексты


def _money(v: float) -> str:
    return f"{float(v):,.0f} ₽".replace(",", " ")


def greeting(client: AdClient, created: bool) -> str:
    who = f", {client.name}" if client.name else ""
    head = (
        f"Здравствуйте{who}! Это САРАФАН — реклама в районных сообществах Кировской области.\n"
        f"Ваш кабинет №{client.id}."
    )
    if created:
        head += " Мы его только что завели — заказывать посты можно прямо здесь, кнопками."
    return head + "\n\nЧто сделать?"


async def balance_text(session, client: AdClient) -> str:
    from modules.ad_cabinet import packages as pkgs

    payments = (
        (await session.execute(select(AdPayment).where(AdPayment.client_id == client.id)))
        .scalars()
        .all()
    )
    pubs = (
        (await session.execute(select(AdPublication).where(AdPublication.client_id == client.id)))
        .scalars()
        .all()
    )
    active = (
        (
            await session.execute(
                select(AdScheduledPost).where(
                    AdScheduledPost.client_id == client.id,
                    AdScheduledPost.status.in_(client_orders.ACTIVE_STATUSES),
                )
            )
        )
        .scalars()
        .all()
    )
    bal = compute_balance(payments, pubs)
    state = await pkgs.get_state(session, client.id)
    lines = [f"💰 Кабинет №{client.id}"]
    lines.append(f"Оплачено: {_money(bal.get('paid', 0))}")
    lines.append(f"Израсходовано: {_money(bal.get('spent', 0))}")
    lines.append(f"Остаток: {_money(bal.get('remaining', 0))}")
    if bal.get("awaiting"):
        lines.append(f"Ожидает оплаты: {_money(bal['awaiting'])}")
    pending = [p for p in active if p.status == "pending"]
    sched = [p for p in active if p.status != "pending"]
    if pending:
        lines.append(f"На одобрении: {len(pending)} пост(ов)")
    if sched:
        lines.append(f"Запланировано: {len(sched)} пост(ов)")
    pkg = state.get("package")
    if pkg is not None and pkg.kind == "unlimited":
        lines.append(
            f"Безлимит до {pkg.period_end:%d.%m.%Y}: до 1 поста в сутки в каждом сообществе"
        )
    elif pkg is not None:
        left = max(0, int(pkg.posts_total or 0) - int(pkg.posts_used or 0))
        lines.append(f"Пакет: осталось {left} из {pkg.posts_total} постов")
    if state.get("block_reason"):
        lines.append(f"⛔ {state['block_reason']}")
    return "\n".join(lines)


def prices_text() -> str:
    from config.ad_landing import PACKAGES, PRICE_SINGLE_RUB

    lines = ["📋 Цены на размещение поста", f"1 сообщество — {_money(PRICE_SINGLE_RUB)}"]
    for p in PACKAGES:
        covers = p.get("covers")
        tail = f" ({covers} сообществ)" if covers else " (вся сеть)"
        lines.append(f"{p.get('title', 'пакет')}{tail} — {_money(p.get('price', 0))}")
    lines.append("\nЦена считается автоматически при заказе: выберите районы — увидите сумму.")
    return "\n".join(lines)


def payments_text() -> str:
    from config.ad_landing import PAYMENTS

    lines = ["💳 Оплата переводом по номеру телефона:"]
    for p in PAYMENTS:
        lines.append(f"• {p['bank']}: {p['phone']} ({p['holder']})")
    lines.append(
        "\nПосле перевода нажмите «✅ Оплатил» — владелец увидит и подтвердит оплату, "
        "подтверждение придёт сюда."
    )
    return "\n".join(lines)


async def regions_list(session) -> List[Tuple[int, str]]:
    rows = (
        await session.execute(
            select(Region.id, Region.name)
            .where(Region.is_active.is_(True), Region.vk_group_id.isnot(None))
            .order_by(Region.name.asc())
        )
    ).all()
    return [(int(rid), name or f"район {rid}") for rid, name in rows]


# ---- районы кнопками (заказ владельца 2026-09-02: «щёлкать мышкой, а не цифры»)

REGION_COLS = 4  # кнопок в ряду
REGION_ROWS = 8  # рядов с районами на странице; ещё 2 ряда — листание и управление
REGION_PAGE = REGION_COLS * REGION_ROWS  # 32 района на страницу (лимит ВК — 40 кнопок)
REGION_LABEL_MAX = 9


def region_label(name: str) -> str:
    """«КИРОВО-ЧЕПЕЦК - ИНФО» → «Кирово-че». По первым буквам район узнаваем."""
    base = re.split(r"\s+[-—–]\s+", name or "", maxsplit=1)[0].strip() or (name or "")
    base = base[:1].upper() + base[1:].lower()
    return base[:REGION_LABEL_MAX]


def regions_keyboard(
    regions: Sequence[Tuple[int, str]], chosen: Sequence[int], page: int = 0
) -> str:
    """Страница районов: выбранные с ✅, листание, «Готово» / «Все районы» / «Отмена»."""
    regions = list(regions)
    pages = max(1, (len(regions) + REGION_PAGE - 1) // REGION_PAGE)
    page = max(0, min(int(page), pages - 1))
    chosen_set = set(int(x) for x in chosen)
    rows: List[List[Dict[str, Any]]] = []
    chunk = regions[page * REGION_PAGE : (page + 1) * REGION_PAGE]
    for i in range(0, len(chunk), REGION_COLS):
        row = []
        for rid, name in chunk[i : i + REGION_COLS]:
            on = rid in chosen_set
            row.append(
                {
                    "action": {
                        "type": "text",
                        "label": ("✅ " if on else "") + region_label(name),
                        "payload": json.dumps({"cmd": CMD_REGION, "id": int(rid)}),
                    },
                    "color": "positive" if on else "secondary",
                }
            )
        rows.append(row)
    if pages > 1:
        nav = []
        if page > 0:
            nav.append(
                {
                    "action": {
                        "type": "text",
                        "label": f"◀ Ещё ({page})",
                        "payload": json.dumps({"cmd": CMD_REGION_PAGE, "p": page - 1}),
                    },
                    "color": "secondary",
                }
            )
        if page < pages - 1:
            nav.append(
                {
                    "action": {
                        "type": "text",
                        "label": f"Ещё ▶ ({pages - page - 1})",
                        "payload": json.dumps({"cmd": CMD_REGION_PAGE, "p": page + 1}),
                    },
                    "color": "secondary",
                }
            )
        rows.append(nav)
    rows.append(
        [
            _btn("✅ Готово", CMD_REGIONS_DONE, "positive"),
            _btn("🌐 Все районы", CMD_ALL_REGIONS, "primary"),
            _btn("❌ Отмена", CMD_CANCEL, "negative"),
        ]
    )
    return keyboard(rows)


def regions_status(regions: Sequence[Tuple[int, str]], chosen: Sequence[int]) -> str:
    names = {rid: name for rid, name in regions}
    picked = [region_label(names[r]) for r in chosen if r in names]
    if not picked:
        return "Выберите районы кнопками (можно несколько), затем «Готово»."
    return (
        f"Выбрано {len(picked)}: " + ", ".join(picked) + ". Ещё районы — кнопками, потом «Готово»."
    )


def regions_prompt(regions: Sequence[Tuple[int, str]]) -> List[str]:
    """Нумерованный список районов, разбитый под лимит ВК."""
    head = (
        "В какие районы? Напишите номера через запятую (например: 1, 4, 7) "
        "или нажмите «Все районы».\n"
    )
    chunks: List[str] = []
    cur = head
    for i, (_rid, name) in enumerate(regions, 1):
        line = f"{i}. {name}\n"
        if len(cur) + len(line) > VK_MSG_MAX:
            chunks.append(cur.rstrip())
            cur = ""
        cur += line
    chunks.append(cur.rstrip())
    return chunks


def parse_region_choice(text: str, regions: Sequence[Tuple[int, str]]) -> List[int]:
    """«1, 4, 7» → id районов; мусор и номера вне списка отбрасываются. Чистая."""
    nums = [int(n) for n in re.findall(r"\d+", text or "")]
    out: List[int] = []
    for n in nums:
        if 1 <= n <= len(regions):
            rid = regions[n - 1][0]
            if rid not in out:
                out.append(rid)
    return out


def parse_when(text: str, *, now_msk: datetime) -> Optional[datetime]:
    """«25.09 14:30» / «25.09.2026 14:30» / «завтра 10:00» → МСК wall-clock. Чистая."""
    t = (text or "").strip().lower()
    if not t:
        return None
    m = re.match(r"^(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\s+(\d{1,2}):(\d{2})$", t)
    if m:
        d, mo, y, h, mi = m.groups()
        try:
            return datetime(int(y) if y else now_msk.year, int(mo), int(d), int(h), int(mi))
        except ValueError:
            return None
    m = re.match(r"^(сегодня|завтра)\s+(\d{1,2}):(\d{2})$", t)
    if m:
        word, h, mi = m.groups()
        base = now_msk.date() + timedelta(days=1 if word == "завтра" else 0)
        try:
            return datetime(base.year, base.month, base.day, int(h), int(mi))
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------- диалог


async def _make_room(session, client_id: int, keep: Sequence[str], need: int) -> None:
    """Перед записью ``need`` файлов вытеснить самые старые, не занятые активными
    постами и черновиком, чтобы клиент только из бота не упёрся в лимит 20
    (кабинета, где «удалите лишние», у него нет). Ошибки глотаются."""
    try:
        have = await asyncio.to_thread(client_photos.client_photo_paths, client_id)
        over = len(have) + int(need) - client_photos.MAX_PHOTOS_PER_CLIENT
        if over <= 0:
            return
        protected = set(await photo_retention.referenced_names(session, client_id)) | set(keep)
        await asyncio.to_thread(client_photos.evict_oldest, client_id, protected, over)
    except Exception:  # noqa: BLE001 - не смогли освободить — сработает лимит
        logger.warning("vk_bot make_room failed", exc_info=True)


async def _collect_photos(
    session,
    client_id: int,
    attachments: Sequence[Dict[str, Any]],
    draft: Dict[str, Any],
    *,
    photo_fetch: PhotoFetch,
) -> Optional[str]:
    """Скачать фото из вложений в библиотеку клиента, дописать имена в ``draft["photos"]``.

    Возвращает заметку клиенту («добавлено N», «не удалось скачать», лимит) или
    ``None``, если фото в сообщении не было. Никогда не бросает — иначе демон
    ответит «что-то пошло не так», и клиент потеряет шаг заказа.
    """
    urls = client_photos.photo_urls_from_attachments(attachments)
    if not urls:
        return None
    photos = [str(n) for n in (draft.get("photos") or []) if n]
    room = client_photos.MAX_PHOTOS_PER_POST - len(photos)
    if room <= 0:
        return f"⚠️ В посте уже {client_photos.MAX_PHOTOS_PER_POST} фото — больше не добавить."
    await _make_room(session, client_id, photos, len(urls[:room]))
    added, failed, problem = 0, 0, None
    for url in urls[:room]:
        try:
            data = await photo_fetch(url)
        except Exception:  # noqa: BLE001 - сеть; фото просто не добавится
            logger.warning("vk_bot photo fetch raised", exc_info=True)
            data = None
        if not data:
            failed += 1
            continue
        try:
            name = await asyncio.to_thread(
                client_photos.store_client_photo,
                client_id,
                data,
                client_photos.BOT_PHOTO_SUFFIX,
            )
        except client_photos.PhotoError as e:
            problem = e.detail
            break
        except Exception:  # noqa: BLE001 - диск; не роняем диалог
            logger.warning("vk_bot photo store failed", exc_info=True)
            problem = "не удалось сохранить фото на сервере"
            break
        photos.append(name)
        added += 1
    draft["photos"] = photos
    parts: List[str] = []
    if added:
        parts.append(
            f"📷 Фото добавлено: {added} (всего {len(photos)} из "
            f"{client_photos.MAX_PHOTOS_PER_POST})."
        )
    if failed:
        parts.append(f"⚠️ Не удалось скачать {failed} фото — пришлите ещё раз.")
    if problem:
        parts.append(f"⚠️ {problem}")
    if len(urls) > room:
        parts.append(f"⚠️ Лишние не взял — в посте до {client_photos.MAX_PHOTOS_PER_POST} фото.")
    return " ".join(parts) or None


def _noted(note: Optional[str], replies: List[Reply]) -> List[Reply]:
    """Приклеить заметку о фото к первому ответу шага (фото пришло с подписью)."""
    if not note or not replies:
        return replies
    text, kb = replies[0]
    return [(note + "\n" + text, kb)] + list(replies[1:])


def _step_hint(step: Optional[str]) -> str:
    """Что делать дальше на шаге заказа — после сообщения «только фото»."""
    if step == STEP_ORDER_TEXT:
        return "Пришлите текст поста — или нажмите «Готово», выпустим только фото."
    if step == STEP_ORDER_REGIONS:
        return "В какие районы? Нажимайте районы кнопками, потом «Готово»."
    if step == STEP_ORDER_WHEN:
        return "Когда выпустить? Нажмите «Сейчас» или напишите дату и время по МСК."
    return "Нажмите «Подтвердить» или «Отмена»."


def _step_keyboard(step: Optional[str], draft: Dict[str, Any]) -> str:
    if step == STEP_ORDER_TEXT:
        return TEXT_DONE_KEYBOARD
    if step == STEP_ORDER_REGIONS:
        regions = [tuple(r) for r in draft.get("regions") or []]
        chosen = [int(x) for x in draft.get("region_ids") or []]
        return regions_keyboard(regions, chosen, int(draft.get("page") or 0))
    if step == STEP_ORDER_WHEN:
        return WHEN_KEYBOARD
    return CONFIRM_KEYBOARD


async def handle(
    session,
    incoming: Incoming,
    state: Optional[Dict[str, Any]],
    *,
    submit: Submitter,
    name_fetch: Optional[NameFetch] = None,
    now_msk: Optional[datetime] = None,
    photo_fetch: Optional[PhotoFetch] = None,
) -> Tuple[List[Reply], Optional[Dict[str, Any]], List[str]]:
    """Один шаг диалога → (ответы, новое состояние, события). Без commit.

    События — ``signup`` (карточка заведена), ``chat`` (сообщение владельцу),
    ``order`` (заказ принят): вызывающий по ним пингует владельца.
    ``photo_fetch`` — как качать фото из вложений; без него вложения не читаются.
    """
    now_msk = now_msk or datetime.utcnow() + timedelta(hours=3)
    client, created = await ensure_client(session, incoming.peer_id, name_fetch=name_fetch)
    events: List[str] = ["signup"] if created else []
    cmd = incoming.command()
    step = (state or {}).get("step")
    draft: Dict[str, Any] = dict((state or {}).get("draft") or {})

    if cmd == CMD_CANCEL:
        if draft.get("photos"):
            # Файлы черновика не должны забивать лимит библиотеки — но библиотека
            # общая с кабинетом: файл, уже выбранный в активный пост там, оставляем
            # (тот же гейт, что у DELETE /api/advertiser/photos).
            in_use = await photo_retention.referenced_names(session, client.id)
            loose = [n for n in draft["photos"] if n and n not in in_use]
            if loose:
                await asyncio.to_thread(client_photos.remove_client_photos, client.id, loose)
        return [("Хорошо, отменил. Что дальше?", MAIN_KEYBOARD)], None, events

    # Кнопки меню работают из любого шага — клиент не обязан помнить, где он.
    if cmd == CMD_BALANCE:
        return [(await balance_text(session, client), MAIN_KEYBOARD)], None, events
    if cmd == CMD_PRICES:
        return [(prices_text(), MAIN_KEYBOARD)], None, events
    if cmd == CMD_PAY:
        return [(payments_text(), MAIN_KEYBOARD)], None, events
    if cmd == CMD_PAID:
        from modules.ad_cabinet import payment_claims

        res = await payment_claims.claim_payments(session, client)
        if res["claimed"]:
            events.append("payment_claimed")
            msg = (
                f"Спасибо! Передал владельцу: {_money(res['amount'])} по "
                f"{res['claimed']} счёт(ам). Подтверждение придёт сюда."
            )
        else:
            msg = "Неоплаченных счетов нет — всё чисто 👍"
        return [(msg, MAIN_KEYBOARD)], None, events
    if cmd == CMD_CABINET:
        return (
            [
                (
                    f"Ваш кабинет №{client.id}: {CABINET_URL}\n"
                    "Вход — через ВКонтакте, той же кнопкой, что здесь.",
                    MAIN_KEYBOARD,
                )
            ],
            None,
            events,
        )
    if cmd == CMD_CHAT:
        return (
            [("Напишите сообщение владельцу — он ответит сюда же.", CANCEL_KEYBOARD)],
            {"step": STEP_CHAT, "draft": {}},
            events,
        )
    if cmd == CMD_ORDER:
        return (
            [
                (
                    "Пришлите текст поста одним сообщением — фото приложите к нему или "
                    f"пришлите следующим сообщением (до {client_photos.MAX_PHOTOS_PER_POST}).",
                    CANCEL_KEYBOARD,
                )
            ],
            {"step": STEP_ORDER_TEXT, "draft": {}},
            events,
        )

    # ---- фото на шагах заказа: качаем сразу (ссылки ВК подписанные и недолгие)
    note: Optional[str] = None
    if step in ORDER_STEPS and photo_fetch is not None and incoming.attachments:
        note = await _collect_photos(
            session, client.id, incoming.attachments, draft, photo_fetch=photo_fetch
        )
        if note is not None:
            state = {"step": step, "draft": draft}  # ветки «остаёмся на шаге» не теряют фото
            if not (incoming.text or "").strip() and cmd is None:
                # Сообщение «только фото»: остаёмся на том же шаге с накопленными фото.
                return (
                    [(note + "\n" + _step_hint(step), _step_keyboard(step, draft))],
                    state,
                    events,
                )

    # ---- шаги
    if step == STEP_CHAT:
        body = (incoming.text or "").strip()
        if not body:
            return [("Сообщение пустое — напишите текст.", CANCEL_KEYBOARD)], state, events
        await chat.post_message(session, client.id, chat.SENDER_CLIENT, body)
        events.append("chat")
        return [("Передал владельцу. Ответ придёт сюда.", MAIN_KEYBOARD)], None, events

    if step == STEP_ORDER_TEXT:
        text = (incoming.text or "").strip()
        photos = draft.get("photos") or []
        if cmd == CMD_REGIONS_DONE:
            text = ""  # «Готово» без текста — пост только из фото (submit_order это допускает)
        if not text and not photos:
            return (
                [("Текст пустой — пришлите текст поста или фото.", CANCEL_KEYBOARD)],
                state,
                events,
            )
        if not text and cmd != CMD_REGIONS_DONE:
            return [(_step_hint(STEP_ORDER_TEXT), TEXT_DONE_KEYBOARD)], state, events
        draft["text"] = text
        regions = await regions_list(session)
        draft["regions"] = regions
        draft["region_ids"] = []
        draft["page"] = 0
        return (
            [
                (
                    (note + "\n" if note else "")
                    + "В какие районы? Нажимайте районы кнопками (можно несколько), потом "
                    "«Готово». Или сразу «Все районы».",
                    regions_keyboard(regions, [], 0),
                )
            ],
            {"step": STEP_ORDER_REGIONS, "draft": draft},
            events,
        )

    if step == STEP_ORDER_REGIONS:
        regions = [tuple(r) for r in draft.get("regions") or await regions_list(session)]
        chosen = [int(x) for x in draft.get("region_ids") or []]
        page = int(draft.get("page") or 0)
        payload = incoming.payload or {}
        if cmd == CMD_REGION:
            try:
                rid = int(payload.get("id"))
            except (TypeError, ValueError):
                rid = None
            if rid is not None and rid in {r for r, _ in regions}:
                chosen = [r for r in chosen if r != rid] if rid in chosen else chosen + [rid]
            draft["region_ids"] = chosen
            return (
                _noted(
                    note,
                    [(regions_status(regions, chosen), regions_keyboard(regions, chosen, page))],
                ),
                {"step": STEP_ORDER_REGIONS, "draft": draft},
                events,
            )
        if cmd == CMD_REGION_PAGE:
            try:
                page = int(payload.get("p") or 0)
            except (TypeError, ValueError):
                page = 0
            draft["page"] = page
            return (
                _noted(
                    note,
                    [(regions_status(regions, chosen), regions_keyboard(regions, chosen, page))],
                ),
                {"step": STEP_ORDER_REGIONS, "draft": draft},
                events,
            )
        if cmd == CMD_ALL_REGIONS:
            chosen = [rid for rid, _ in regions]
        elif cmd == CMD_REGIONS_DONE:
            pass
        else:
            typed = parse_region_choice(incoming.text, regions)
            if typed:
                chosen = typed
        if not chosen:
            return (
                _noted(
                    note,
                    [
                        (
                            "Пока ни один район не выбран — нажмите районы кнопками или "
                            "«Все районы».",
                            regions_keyboard(regions, chosen, page),
                        )
                    ],
                ),
                state,
                events,
            )
        draft["region_ids"] = chosen
        return (
            _noted(
                note,
                [
                    (
                        f"Выбрано районов: {len(chosen)}. Когда выпустить? "
                        "Нажмите «Сейчас» или напишите "
                        "дату и время по МСК: 25.09 14:30 (можно «завтра 10:00»).",
                        WHEN_KEYBOARD,
                    )
                ],
            ),
            {"step": STEP_ORDER_WHEN, "draft": draft},
            events,
        )

    if step == STEP_ORDER_WHEN:
        if cmd == CMD_NOW:
            draft["publish_now"] = True
            draft["publish_at"] = None
        else:
            when = parse_when(incoming.text, now_msk=now_msk)
            if when is None:
                return (
                    _noted(
                        note,
                        [
                            (
                                "Не понял дату. Формат: 25.09 14:30 (МСК) — или нажмите «Сейчас».",
                                WHEN_KEYBOARD,
                            )
                        ],
                    ),
                    state,
                    events,
                )
            draft["publish_now"] = False
            draft["publish_at"] = when.isoformat()
        from modules.ad_cabinet.pricing import quote_for_client

        n = len(draft.get("region_ids") or [])
        q = await quote_for_client(session, client.id, n, now_msk=now_msk)
        when_txt = "сейчас" if draft.get("publish_now") else draft["publish_at"].replace("T", " ")
        text_full = draft.get("text") or ""
        preview = (text_full[:300] + ("…" if len(text_full) > 300 else "")) or "(без текста)"
        photos_n = len(draft.get("photos") or [])
        disc = (
            f" (скидка {q['discount_pct']} %, по прайсу {_money(q['base_price'])})"
            if q.get("discount_pct")
            else ""
        )
        return (
            _noted(
                note,
                [
                    (
                        f"Проверьте заказ:\n— районов: {n}\n— выход: {when_txt}\n"
                        f"— цена: {_money(q['price'])}{disc}"
                        + (" (или в счёт вашего пакета)" if q["price"] else "")
                        + (f"\n— фото: {photos_n}" if photos_n else "")
                        + f"\n\nТекст:\n{preview}",
                        CONFIRM_KEYBOARD,
                    )
                ],
            ),
            {"step": STEP_ORDER_CONFIRM, "draft": draft},
            events,
        )

    if step == STEP_ORDER_CONFIRM:
        if cmd != CMD_CONFIRM:
            photos_n = len(draft.get("photos") or [])
            hint = "Нажмите «Подтвердить» или «Отмена»." + (
                f" В заказе фото: {photos_n}." if note and photos_n else ""
            )
            return _noted(note, [(hint, CONFIRM_KEYBOARD)]), state, events
        try:
            result = await submit(session, client, draft)
        except client_orders.OrderError as e:
            log_interaction(
                session,
                kind="cabinet_order_refused",
                client_id=client.id,
                summary=f"Заказ из ВК-бота не прошёл: {e}",
                actor="client",
            )
            tail = " Фото остались в вашем кабинете." if draft.get("photos") else ""
            return [(f"Не получилось: {e}{tail}", MAIN_KEYBOARD)], None, events
        posts = result.get("posts") or []
        n = len(posts)
        failed_posts = [p for p in posts if getattr(p, "status", None) == "failed"]
        price = float(result.get("price_total") or 0)
        log_interaction(
            session,
            kind="client_order",
            client_id=client.id,
            summary=f"Заказ из ВК-бота: {n} районов, {price:.0f} ₽"
            + (" (на модерации)" if result.get("moderation") else ""),
            actor="client",
            meta={"source": "vk_bot", "order_ref": result.get("order_ref")},
        )
        # «order» — на одобрение; «order_direct» — trusted, уже в VK-отложке
        # (аудит 2026-09-05: владельцу говорили «ждёт одобрения», а очередь была пуста).
        events.append("order" if result.get("moderation") else "order_direct")
        if result.get("moderation"):
            msg = (
                f"Заказ принят: {n} районов, {_money(price)}. Владелец проверит пост и "
                "подтвердит — сообщение придёт сюда."
            )
        elif failed_posts and len(failed_posts) == n:
            # trusted-клиент, а ВК не принял (нет user-токена, битое фото): честно,
            # а не «в очереди» — строки failed видны владельцу в /ad.
            why = str(getattr(failed_posts[0], "error_message", "") or "ошибка публикации")[:120]
            msg = f"ВК не принял посты: {why}. Владелец уведомлён и разберётся."
        elif failed_posts:
            msg = (
                f"Готово: {n - len(failed_posts)} постов в очереди на {_money(price)}, "
                f"{len(failed_posts)} ВК не принял — владелец разберётся."
            )
        else:
            msg = f"Готово: {n} постов поставлены в очередь на {_money(price)}."
        return [(msg, MAIN_KEYBOARD)], None, events

    # Свободный текст вне шага — это сообщение владельцу, а не повод показать
    # приветствие (аудит 2026-09-05: «алло, я оплатил» уходило в никуда).
    # Новому клиенту — приветствие с меню: его первое «здравствуйте» не письмо.
    body = (incoming.text or "").strip()
    if body and not created:
        await chat.post_message(session, client.id, chat.SENDER_CLIENT, body)
        events.append("chat")
        return [("Передал владельцу. Ответ придёт сюда.", MAIN_KEYBOARD)], None, events
    if (
        not body
        and not created
        and client_photos.photo_urls_from_attachments(incoming.attachments, limit=1)
    ):
        # Фото без слов вне заказа (часто — скрин перевода): не сохраняем и не
        # заводим заказ сами, подсказываем куда его.
        return (
            [
                (
                    "Фото получил, но пока не знаю, к чему оно. Для поста нажмите "
                    "«🛒 Заказать пост» и пришлите фото вместе с текстом. Если это для "
                    "владельца (например, скрин перевода) — напишите пару слов текстом, "
                    "я передам; само фото он увидит в этом диалоге.",
                    MAIN_KEYBOARD,
                )
            ],
            None,
            events,
        )
    return [(greeting(client, created), MAIN_KEYBOARD)], None, events


__all__ = [
    "Incoming",
    "handle",
    "ensure_client",
    "find_client",
    "parse_payload",
    "parse_region_choice",
    "parse_when",
    "regions_prompt",
    "MAIN_KEYBOARD",
    "BUTTON_TEXT",
    "regions_keyboard",
    "region_label",
]
