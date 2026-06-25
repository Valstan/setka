"""Баланс нити клиента «оплачено / израсходовано / осталось» (Раунд 4+, И1).

Запрос владельца — вести клиента непрерывной нитью приём → оплата → публикация
→ контроль → напоминание о доплате, не теряя его из вида. Ядро — баланс между
полученными деньгами и стоимостью уже вышедших публикаций.

**Вычисляется из существующих полей, НЕ материализуется в новую таблицу-источник
правды.** Урок ``AdOrderItem`` (миграция 030): третий несинхронизированный учётный
журнал = боль (его ``quantity`` ни с деньгами, ни с фактом публикаций не сводится).
Поэтому баланс — чистая производная:

  * **приход** (``paid``)   = Σ ``ad_payments.amount`` при ``status='paid'``;
  * **расход** (``spent``)  = Σ ``ad_publications.price`` при ``status='published'``
    и ``price IS NOT NULL``;
  * **остаток** (``remaining``) = приход − расход;
  * **ratio** = расход / приход — сигнал «пора напомнить о доплате» (порог в И2);
  * **spend_incomplete** — есть вышедшие публикации без проставленной цены: расход
    недосчитан, остаток оптимистичен. Не маскируем — помечаем, оператор проставит цену.

Единый источник правды ``paid``: чинит расхождение, при котором список клиентов
считал ``status=='paid'``, а карточка — ``status!='awaiting'`` (эквивалентны лишь
пока статусов ровно два; ``=='paid'`` — явная конвенция списка и воронки).

Чистые функции без БД/сети: принимают любые объекты с атрибутами ``.status`` +
``.amount`` (оплаты) / ``.status`` + ``.price`` (публикации) — ORM-строки или фейки
из тестов.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable

PAID_STATUS = "paid"
AWAITING_STATUS = "awaiting"
PUBLISHED_STATUS = "published"

# Порог «расход догнал оплату» для UI-сигнала и beat-напоминания (И2 переопределит
# через env AD_SPEND_ALERT_RATIO). Доля израсходованного от оплаченного.
SPEND_NEAR_RATIO = 0.8

# Допуск на копеечный дрейф float при сравнении остатка с нулём.
_EPS = 0.005


def _amount(obj: Any) -> float:
    a = getattr(obj, "amount", None)
    return float(a) if a is not None else 0.0


def summarize(
    paid: float,
    spent: float,
    *,
    awaiting: float = 0.0,
    published_unpriced: int = 0,
    paid_units: int = 0,
    consumed_units: int = 0,
) -> Dict[str, Any]:
    """Свести баланс из уже посчитанных сумм. Уровень: ok / near / over.

    Вынесено отдельно, чтобы список клиентов (скалярные подзапросы-агрегаты) и
    карточка (полные строки) считали уровень/остаток ОДНОЙ логикой.

    Два измерения:
      * **рубли** — приход/расход/остаток в деньгах (уровень near при ratio≥0.8);
      * **штуки** (``units``) — пакет публикаций «куплено N, вышло M, осталось K».
        Решение владельца 2026-06-25: основной учёт — штучный, напоминание о
        доплате — при ПЕРЕРАСХОДЕ (вышло больше оплаченного → ``units.over``).

    ``units.tracked`` = у клиента вообще зафиксирован пакет (``paid_units>0``);
    пока оператор не проставил «за сколько публикаций» — штучный баланс молчит.
    """
    remaining = paid - spent
    if paid > 0:
        ratio: Any = spent / paid
    elif spent > 0:
        ratio = None  # расход без единой оплаты — особый случай «over»
    else:
        ratio = 0.0

    if remaining < -_EPS or (paid <= 0 and spent > 0):
        level = "over"  # перерасход / расход без оплаты — нужна доплата срочно
    elif ratio is not None and ratio >= SPEND_NEAR_RATIO:
        level = "near"  # расход подобрался к оплате — пора напомнить
    else:
        level = "ok"

    units_remaining = int(paid_units) - int(consumed_units)
    units_tracked = int(paid_units) > 0
    units_over = units_tracked and units_remaining < 0

    return {
        "paid": round(paid, 2),
        "awaiting": round(awaiting, 2),
        "spent": round(spent, 2),
        "remaining": round(remaining, 2),
        "ratio": round(ratio, 4) if ratio is not None else None,
        "spend_incomplete": published_unpriced > 0,
        "published_unpriced": int(published_unpriced),
        "level": level,
        "needs_topup": level in ("near", "over"),
        "units": {
            "paid": int(paid_units),
            "consumed": int(consumed_units),
            "remaining": units_remaining,
            "tracked": units_tracked,
            "over": units_over,
        },
    }


def compute_balance(
    payments: Iterable[Any],
    publications: Iterable[Any],
) -> Dict[str, Any]:
    """Баланс нити по полным строкам оплат и публикаций клиента."""
    paid = 0.0
    awaiting = 0.0
    paid_units = 0
    for p in payments:
        status = getattr(p, "status", None)
        if status == PAID_STATUS:
            paid += _amount(p)
            units = getattr(p, "units_paid", None)
            if units:
                paid_units += int(units)
        elif status == AWAITING_STATUS:
            awaiting += _amount(p)

    spent = 0.0
    unpriced = 0
    consumed_units = 0
    for pub in publications:
        if getattr(pub, "status", None) != PUBLISHED_STATUS:
            continue  # снятые (removed) в расход не идут
        consumed_units += 1  # каждая вышедшая публикация — 1 размещение пакета
        price = getattr(pub, "price", None)
        if price is None:
            unpriced += 1
        else:
            spent += float(price)

    return summarize(
        paid,
        spent,
        awaiting=awaiting,
        published_unpriced=unpriced,
        paid_units=paid_units,
        consumed_units=consumed_units,
    )
