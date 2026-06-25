"""Тесты баланса нити клиента «оплачено / израсходовано / осталось» (И1).

Чистые функции без БД/сети — гоняем на ORM-объектах и числах напрямую.
"""

from __future__ import annotations

from database.models import AdPayment, AdPublication
from modules.ad_cabinet.balance import compute_balance, summarize


def _pay(amount, status="paid"):
    return AdPayment(client_id=1, amount=amount, status=status)


def _pub(price, status="published"):
    return AdPublication(client_id=1, community_vk_id=-100, price=price, status=status)


def test_basic_remaining_and_near_level():
    # Оплачено 10000, расход 3×3000=9000 → осталось 1000, ratio 0.9 → near.
    bal = compute_balance(
        [_pay(10000)],
        [_pub(3000), _pub(3000), _pub(3000)],
    )
    assert bal["paid"] == 10000.0
    assert bal["spent"] == 9000.0
    assert bal["remaining"] == 1000.0
    assert bal["ratio"] == 0.9
    assert bal["level"] == "near"
    assert bal["needs_topup"] is True
    assert bal["spend_incomplete"] is False


def test_awaiting_excluded_from_paid():
    bal = compute_balance([_pay(5000, "paid"), _pay(2000, "awaiting")], [])
    assert bal["paid"] == 5000.0
    assert bal["awaiting"] == 2000.0
    assert bal["spent"] == 0.0
    assert bal["level"] == "ok"
    assert bal["needs_topup"] is False


def test_removed_publication_not_counted_as_spend():
    bal = compute_balance([_pay(5000)], [_pub(3000, "removed"), _pub(1000, "published")])
    assert bal["spent"] == 1000.0  # снятая не идёт в расход
    assert bal["remaining"] == 4000.0


def test_published_without_price_flags_incomplete():
    bal = compute_balance([_pay(5000)], [_pub(None), _pub(2000)])
    assert bal["spent"] == 2000.0  # безценовая в сумму не идёт
    assert bal["published_unpriced"] == 1
    assert bal["spend_incomplete"] is True


def test_overspend_is_over_level():
    bal = compute_balance([_pay(1000)], [_pub(1500)])
    assert bal["remaining"] == -500.0
    assert bal["level"] == "over"
    assert bal["needs_topup"] is True


def test_spend_without_payment_is_over_with_null_ratio():
    bal = compute_balance([], [_pub(800)])
    assert bal["paid"] == 0.0
    assert bal["spent"] == 800.0
    assert bal["ratio"] is None  # расход без единой оплаты
    assert bal["level"] == "over"


def test_low_usage_is_ok():
    bal = compute_balance([_pay(10000)], [_pub(1000)])
    assert bal["ratio"] == 0.1
    assert bal["level"] == "ok"
    assert bal["needs_topup"] is False


def test_empty_is_ok_zero():
    bal = compute_balance([], [])
    assert bal == {
        "paid": 0.0,
        "awaiting": 0.0,
        "spent": 0.0,
        "remaining": 0.0,
        "ratio": 0.0,
        "spend_incomplete": False,
        "published_unpriced": 0,
        "level": "ok",
        "needs_topup": False,
    }


def test_summarize_matches_list_path():
    # Список клиентов зовёт summarize по агрегатам — та же логика уровня.
    bal = summarize(10000.0, 8500.0, awaiting=0.0)
    assert bal["remaining"] == 1500.0
    assert bal["level"] == "near"  # 0.85 >= 0.8
