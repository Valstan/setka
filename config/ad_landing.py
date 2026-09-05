"""Контент публичного лендинга /regions/links — вкладка «Реклама и цены».

ЕДИНСТВЕННОЕ место, где живут цены, скидки, реквизиты и контакты лендинга:
владелец правит этот файл — страница меняется после restart, без правок шаблона.

Цены и контакты — РЕАЛЬНЫЕ, выданы владельцем 2026-07-29 (переписка в сессии
«публичный лендинг САРАФАН, итерация 3»). База: 350 ₽ за пост в одном
сообществе; пакеты пересчитаны от неё.
"""

from __future__ import annotations

from typing import Any, Dict, List

# Черновик-режим: True → на вкладке цен висит приписка «актуальные цены
# уточняйте при заказе». Цены реальные — выключено.
PRICES_ARE_DRAFT = False

# Базовая цена одного рекламного поста в одном районном сообществе, ₽.
PRICE_SINGLE_RUB = 350

# Пол цены одного размещения после всех скидок (решение владельца 2026-09-05):
# ниже 200 ₽ за пост в сообществе не опускаемся ни скидками, ни операторской
# ценой (0 — «бесплатно» — разрешён отдельно).
PRICE_FLOOR_RUB = 200
# Закреп поста на сутки в одном сообществе — фиксированная доплата, пакетом и
# скидками не покрывается (SINGLE_SERVICES «+200 ₽»).
PIN_PRICE_RUB = 200
# Накопительная скидка (владелец 2026-09-05): 5 % за каждые 3 оплаченных поста
# в календарном месяце (МСК), потолок 30 %, сгорает 1-го числа.
DISCOUNT_STEP_POSTS = 3
DISCOUNT_STEP_PCT = 5
DISCOUNT_MONTH_CAP_PCT = 30
# Постоянным (≥ REGULAR_AFTER_POSTS оплаченных постов за всё время) — +10 %
# бессрочно. Скидки складываются; пол цены защищает снизу.
REGULAR_AFTER_POSTS = 10
REGULAR_PCT = 10

# Тарифы по сети. Каждый: название, что входит, цена, старая цена (для
# зачёркнутой экономии; None = не показывать), бейдж, список фич и ``covers`` —
# на сколько сообществ рассчитан тариф (None = вся сеть). ``covers`` читает
# панель выбора на лендинге: отметил N сообществ галочками — она считает
# дешёвший вариант «N × 350 ₽ или подходящий пакет».
PACKAGES: List[Dict[str, Any]] = [
    {
        "title": "Один пост",
        "subtitle": "в одном сообществе на выбор",
        "price": 350,
        "covers": 1,
        "old_price": None,
        "badge": None,
        "features": [
            "Размещение в любом районном ИНФО-сообществе",
            "Пост остаётся в ленте навсегда",
            "Публикация в согласованное время",
        ],
    },
    {
        "title": "Пакет «Куст»",
        "subtitle": "5 соседних районов",
        "price": 1300,
        "covers": 5,
        "old_price": 1750,
        "badge": "выгода 450 ₽",
        "features": [
            "Один пост в 5 сообществах на выбор",
            "Идеально для событий и услуг «на округу»",
            "Соседние районы = ваша реальная аудитория",
        ],
    },
    {
        "title": "Пакет «Десятка»",
        "subtitle": "10 сообществ",
        "price": 2500,
        "covers": 10,
        "old_price": 3500,
        "badge": "выгода 1000 ₽",
        "features": [
            "Один пост в 10 сообществах на выбор",
            "Частый вопрос «сколько стоит 10?» — вот ответ",
            "Свободная комбинация районов и областных групп",
        ],
    },
    {
        "title": "Вся сеть",
        "subtitle": "все сообщества САРАФАНА",
        "price": 5000,
        "covers": None,
        "old_price": 9100,
        "badge": "лучшая цена",
        "features": [
            "Один пост во всех активных сообществах сети",
            "Максимальный охват двух областей разом",
            "Новые районы попадают в пакет автоматически",
        ],
    },
]

# Поштучные услуги в одном сообществе (помимо пакетов).
SINGLE_SERVICES: List[Dict[str, Any]] = [
    {"title": "Пост в ленте", "price": "350 ₽"},
    {"title": "Закреп на сутки", "price": "+200 ₽"},
    {"title": "Сторис / клип (ваш материал)", "price": "300 ₽"},
    {"title": "Нативный обзор — текст напишем сами", "price": "700 ₽"},
]

# Доп. условия/скидки — список строк, рендерится маркированным списком.
EXTRAS: List[str] = [
    "По каждому посту пришлём отчёт со скринами — бесплатно.",
    "Накопительная скидка: за каждые 3 оплаченных поста в месяце — минус 5 % на "
    "следующие заказы, до 30 %. Считается с 1-го числа заново.",
    "Постоянным клиентам (от 10 оплаченных постов) — ещё минус 10 % навсегда.",
    "Скидки складываются, но цена одного размещения не опускается ниже 200 ₽.",
    "Безлимит на 30 дней — 5000 ₽: любые сообщества сети, до одного поста в " "сутки в каждом.",
    "Поможем бесплатно: подскажем, в каких районах живёт ваша аудитория.",
]

# Контакты для заказа. type — иконка на странице; url = None → просто текст.
CONTACTS: List[Dict[str, Any]] = [
    {
        "type": "vk",
        "label": "ВКонтакте",
        "value": "vk.ru/valstan_valstan",
        "url": "https://vk.ru/valstan_valstan",
    },
    {
        "type": "telegram",
        "label": "Телеграм",
        "value": "t.me/Val_Stanley",
        "url": "https://t.me/Val_Stanley",
    },
    {
        "type": "phone",
        "label": "Телефон",
        "value": "+7 922 900-59-10",
        "url": "tel:+79229005910",
    },
    {
        "type": "email",
        "label": "Почта",
        "value": "valstan@valstan.ru",
        "url": "mailto:valstan@valstan.ru",
    },
    {
        "type": "max",
        "label": "MAX",
        "value": "8 922 900-59-10",
        "url": "tel:+79229005910",
    },
]

# Оплата — «выберите удобный банк». Телефоны кликабельные (tel:), чтобы с
# телефона сразу открывался перевод по номеру.
PAYMENTS: List[Dict[str, Any]] = [
    {
        "bank": "Альфа-Банк",
        "phone": "+7 922 900-59-10",
        "phone_url": "tel:+79229005910",
        "holder": "Савиных Валентин Владимирович",
    },
    {
        "bank": "Сбербанк",
        "phone": "+7 922 909-94-69",
        "phone_url": "tel:+79229099469",
        "holder": "Савиных Александр Владимирович",
    },
    {
        "bank": "Т-Банк",
        "phone": "+7 922 969-90-29",
        "phone_url": "tel:+79229699029",
        "holder": "Савиных Виталина Валентиновна",
    },
]

# Рекламная картинка. Владелец решил 2026-07-29: не нужна (инфа на старой
# устарела, страница сама выглядит как реклама). Оставлено на будущее:
# положить файл в web/static/img/ и указать "/static/img/ad.jpg".
AD_IMAGE_URL: str | None = None


# Максимум строк в таблице цен. Считаем с запасом: сеть растёт, а таблица
# должна покрывать любой выбор посетителя (сверх потолка цена всё равно та же).
PRICE_TABLE_MAX = 80


def quote_price(n: int) -> Dict[str, Any]:
    """Цена заказа на ``n`` сообществ — ровно так, как её считает владелец.

    Правило (заказ владельца 2026-07-29): цена набегает по ``PRICE_SINGLE_RUB``
    за сообщество, но на рубежах пакетов **сбрасывается** до пакетной цены и
    дальше снова растёт поштучно; сверху всё ограничено ценой «вся сеть».

    Отсюда 9 сообществ (1300 + 4×350 = 2700) дороже 10 (2500) — это не баг, а
    смысл акции: на рубеже включается опт. Лендинг на таких местах прямо
    подсказывает «добавьте ещё одно — станет дешевле».

    Возвращает ``{n, price, anchor, saved, percent, capped}``: ``anchor`` — из
    какого тарифа считали, ``saved``/``percent`` — выгода против поштучной
    цены, ``capped`` — упёрлись в потолок «всей сети».
    """
    n = max(0, int(n))
    plain = PRICE_SINGLE_RUB * n
    if n == 0:
        return {"n": 0, "price": 0, "anchor": None, "saved": 0, "percent": 0, "capped": False}

    whole = next((p for p in PACKAGES if p.get("covers") is None), None)
    cap = whole["price"] if whole else None

    # Якорь — самый большой пакет, который уже «покрыт» выбором (covers <= n).
    anchor = None
    for pkg in PACKAGES:
        covers = pkg.get("covers")
        if covers is None or covers > n:
            continue
        if anchor is None or covers > anchor["covers"]:
            anchor = pkg
    if anchor is None:  # тарифов нет вовсе — считаем поштучно
        price, anchor_title = plain, None
    else:
        price = anchor["price"] + PRICE_SINGLE_RUB * (n - anchor["covers"])
        anchor_title = anchor["title"]

    capped = cap is not None and price >= cap
    if capped:
        price, anchor_title = cap, whole["title"]

    saved = max(0, plain - price)
    return {
        "n": n,
        "price": price,
        "anchor": anchor_title,
        "saved": saved,
        "percent": round(saved / plain * 100) if plain else 0,
        "capped": capped,
    }


def build_price_table(max_n: int = PRICE_TABLE_MAX) -> List[Dict[str, Any]]:
    """Предрасчитанные цены 1..max_n — их читает панель выбора на лендинге.

    Считает сервер, а не браузер: правило скидок живёт в одном месте (эта
    функция, покрытая тестами), клиент только показывает готовое число.
    """
    return [quote_price(n) for n in range(1, max_n + 1)]


def cap_starts_at(max_n: int = PRICE_TABLE_MAX) -> int | None:
    """С какого количества сообществ цена упирается в потолок «всей сети»."""
    for row in build_price_table(max_n):
        if row["capped"]:
            return row["n"]
    return None


def discount_pct(paid_month: int, paid_total: int) -> Dict[str, int]:
    """Скидка клиента по оплаченным постам: за месяц (ступени) и постоянная.

    Возвращает ``{"month": %, "regular": %, "total": %}``; ``next_step_posts`` —
    сколько оплаченных постов до следующей ступени (0 — потолок).
    """
    paid_month = max(0, int(paid_month))
    paid_total = max(0, int(paid_total))
    month = min(DISCOUNT_MONTH_CAP_PCT, (paid_month // DISCOUNT_STEP_POSTS) * DISCOUNT_STEP_PCT)
    regular = REGULAR_PCT if paid_total >= REGULAR_AFTER_POSTS else 0
    if month >= DISCOUNT_MONTH_CAP_PCT:
        next_step = 0
    else:
        next_step = DISCOUNT_STEP_POSTS - (paid_month % DISCOUNT_STEP_POSTS)
    return {
        "month": month,
        "regular": regular,
        "total": month + regular,
        "next_step_posts": next_step,
    }


def apply_discount(base_total: int | float, n: int, pct: int) -> Dict[str, Any]:
    """Применить скидку ``pct`` к цене ``n`` размещений с полом ``PRICE_FLOOR_RUB``.

    Пол — ``n × PRICE_FLOOR_RUB``, но не выше базовой цены: тариф «вся сеть»
    (5000 за все сообщества) дешевле пола на 38 районов и остаётся как есть.
    Итог округляется до рубля.
    """
    n = max(0, int(n))
    base = float(base_total)
    if n == 0 or base <= 0:
        return {"price": 0, "discount_pct": 0, "floor_applied": False, "saved": 0}
    pct = max(0, min(100, int(pct)))
    discounted = round(base * (100 - pct) / 100)
    floor_total = min(base, PRICE_FLOOR_RUB * n)
    price = int(max(discounted, floor_total))
    return {
        "price": price,
        "discount_pct": pct,
        "floor_applied": bool(pct and price > discounted),
        "saved": int(round(base - price)),
    }


def get_ad_landing_context() -> Dict[str, Any]:
    """Готовый контекст для шаблона лендинга."""
    return {
        "prices_are_draft": PRICES_ARE_DRAFT,
        "price_single": PRICE_SINGLE_RUB,
        "packages": PACKAGES,
        "single_services": SINGLE_SERVICES,
        "extras": EXTRAS,
        "contacts": CONTACTS,
        "payments": PAYMENTS,
        "ad_image_url": AD_IMAGE_URL,
        "price_table": build_price_table(),
        "cap_from": cap_starts_at(),
    }
