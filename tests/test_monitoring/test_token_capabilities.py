"""Тесты замера прав токенов (beat раз в месяц, решение владельца 2026-07-26).

Покрываем два чистых решения модуля:

* ``select_tokens`` — кого вообще меряем. Community-токены ведут себя одинаково
  (замер 25.07 подтвердил это на 19 ключах), поэтому меряем все user-токены и
  лишь два community: полный перебор жёг бы квоту без новой информации.
* ``diff_snapshots`` — ради чего заводится периодический замер. Сама матрица
  меняется редко; ценность в том, чтобы дрейф («вчера читал стену, сегодня
  err27») не прошёл незамеченным до сломанного сбора.

Сеть не трогаем: ``call``/``measure`` бьют в VK и проверяются на живом проде.
"""

from __future__ import annotations

import os

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from modules.vk_monitor import token_capabilities as caps  # noqa: E402


def test_select_takes_all_user_tokens():
    selected = caps.select_tokens({"VALSTAN": "a", "MAMA": "b", "VITA": "c"})
    assert set(selected) == {"VALSTAN", "MAMA", "VITA"}


def test_select_samples_only_two_communities():
    """19 community-ключей дают одну и ту же матрицу — меряем два, не все."""
    tokens = {"MAMA": "u"}
    tokens.update({f"COMM_{100 + i}": f"c{i}" for i in range(19)})
    selected = caps.select_tokens(tokens)
    community = [name for name in selected if name.startswith("COMM_")]
    assert len(community) == caps.COMMUNITY_SAMPLE
    assert "MAMA" in selected


def test_select_is_stable_across_runs():
    """Выборка детерминированная — иначе дифф покажет ложный дрейф."""
    tokens = {f"COMM_{i}": "x" for i in (300, 100, 200)}
    first = list(caps.select_tokens(tokens))
    second = list(caps.select_tokens(dict(reversed(list(tokens.items())))))
    assert first == second


def test_diff_without_previous_is_silent():
    """Первый замер не объявляет всё подряд изменением."""
    assert caps.diff_snapshots(None, {"MAMA": {"wall.get(своё)": "ok"}}) == []


def test_diff_reports_capability_loss():
    previous = {"MAMA": {"wall.get(своё)": "ok", "users.get": "ok"}}
    current = {"MAMA": {"wall.get(своё)": "err27", "users.get": "ok"}}
    changes = caps.diff_snapshots(previous, current)
    assert changes == ["MAMA · wall.get(своё): ok → err27"]


def test_diff_reports_new_and_missing_tokens():
    previous = {"VITA": {"users.get": "ok"}}
    current = {"MAMA": {"users.get": "ok"}}
    changes = caps.diff_snapshots(previous, current)
    assert "MAMA: новый токен в замере" in changes
    assert "VITA: пропал из замера" in changes


def test_diff_ignores_probes_absent_in_previous():
    """Добавили новую пробу — это не дрейф прав, а изменение набора."""
    previous = {"MAMA": {"users.get": "ok"}}
    current = {"MAMA": {"users.get": "ok", "groups.search": "ok"}}
    assert caps.diff_snapshots(previous, current) == []


def test_probes_are_read_only():
    """Ни одна проба не пишет: замер не должен публиковать или слать сообщения."""
    forbidden = ("post", "send", "add", "delete", "edit", "create", "join", "leave")
    for _label, method, _params in caps.build_probes():
        tail = method.split(".", 1)[1].lower()
        assert not tail.startswith(forbidden), f"{method} похож на пишущий метод"
