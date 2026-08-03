"""Unit tests для ``scripts/list_gateway_keys.py`` — перечень потребителей шлюза.

Перечень существует, потому что ручной реестр разошёлся с БД и три недели
показывал «двери нет» там, где ключ был активен (мандат brain 2026-08-03).
Значит покрываем ровно те свойства, поломка которых вернёт ту же ошибку:

* **секрет не печатается ни в одном формате.** Вывод уходит наружу — письмом
  мозгу; в отличие от ``issue_gateway_key.py``, где секрет показывается один
  раз оператору. Здесь его нет даже на входе функции отрисовки.
* **env-ключ без записи в БД виден.** Шлюз такой ключ принимает
  (bootstrap-fallback), и умолчание о нём — ровно тот класс расхождения, ради
  которого перечень и делается.
* **выдача ключа не считается использованием.** ``endpoint='issue-key'`` пишет
  сам оператор; засчитав её, перечень показывал бы «потребитель ходил», когда
  ходил администратор.

БД не нужна — ``build_rows``/``render`` чистые. Скрипт вне устанавливаемого
пакета, грузим через importlib (как ``test_register_oidc_client.py``).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "list_gateway_keys", REPO_ROOT / "scripts" / "list_gateway_keys.py"
)
list_gateway_keys = importlib.util.module_from_spec(_spec)
sys.modules["list_gateway_keys"] = list_gateway_keys
_spec.loader.exec_module(list_gateway_keys)

build_rows = list_gateway_keys.build_rows
render = list_gateway_keys.render

SECRET = "sk-super-secret-value-0123456789"

DB_KEYS = [
    {
        "name": "VMALMYZHE",
        "is_active": True,
        "created_at": datetime(2026, 7, 13, 10, 30),
        "rotated_at": None,
        "note": "портал вМалмыже",
    },
    {
        "name": "KAZANSKAYA",
        "is_active": False,
        "created_at": datetime(2026, 7, 12, 9, 0),
        "rotated_at": datetime(2026, 7, 20, 12, 0),
        "note": None,
    },
]


class TestBuildRows:
    def test_db_key_carries_dates_and_active_flag(self):
        rows = {r["name"]: r for r in build_rows(DB_KEYS, [], {})}
        assert rows["VMALMYZHE"]["source"] == "db"
        assert rows["VMALMYZHE"]["is_active"] is True
        assert rows["KAZANSKAYA"]["is_active"] is False
        assert rows["KAZANSKAYA"]["rotated_at"] == datetime(2026, 7, 20, 12, 0)

    def test_key_present_in_both_db_and_env_is_marked(self):
        rows = {r["name"]: r for r in build_rows(DB_KEYS, ["vmalmyzhe"], {})}
        assert rows["VMALMYZHE"]["source"] == "db+env"

    def test_env_only_key_is_not_hidden(self):
        """Шлюз принимает env-ключ без записи в БД — перечень обязан его показать."""
        rows = {r["name"]: r for r in build_rows(DB_KEYS, ["GONBA"], {})}
        assert rows["GONBA"]["source"] == "env"
        assert rows["GONBA"]["created_at"] is None
        assert "в БД записи нет" in rows["GONBA"]["note"]

    def test_usage_is_attached_by_key_name(self):
        usage = {"VMALMYZHE": {"last_used": datetime(2026, 8, 3, 8, 15), "calls": 42}}
        rows = {r["name"]: r for r in build_rows(DB_KEYS, [], usage)}
        assert rows["VMALMYZHE"]["last_used"] == datetime(2026, 8, 3, 8, 15)
        assert rows["VMALMYZHE"]["calls"] == 42
        assert rows["KAZANSKAYA"]["last_used"] is None
        assert rows["KAZANSKAYA"]["calls"] == 0

    def test_rows_are_sorted_by_name(self):
        names = [r["name"] for r in build_rows(DB_KEYS, ["ZZZ", "AAA"], {})]
        assert names == sorted(names)

    def test_operator_endpoints_are_excluded_from_usage_query(self):
        """Выдача ключа — действие оператора, а не потребителя (см. docstring)."""
        assert "issue-key" in list_gateway_keys._OPERATOR_ENDPOINTS


class TestRender:
    def _rows(self):
        usage = {"VMALMYZHE": {"last_used": datetime(2026, 8, 3, 8, 15), "calls": 42}}
        return build_rows(DB_KEYS, ["GONBA"], usage)

    def test_no_secret_leaks_in_any_format(self):
        """Худший случай: оператор вписал секрет в ``note``. Вывод обязан уцелеть.

        Поэтому ``note`` не рендерится вовсе — безопасность вывода, уходящего
        наружу, не должна зависеть от дисциплины заполнения заметок.
        """
        rows = self._rows()
        for row in rows:
            row["note"] = f"{row['note']} {SECRET}".strip()
        for fmt in ("table", "md", "json"):
            out = render(rows, fmt, 90)
            assert SECRET not in out, fmt
            assert "secret" not in out.lower(), fmt

    def test_table_lists_every_key(self):
        out = render(self._rows(), "table", 90)
        for name in ("VMALMYZHE", "KAZANSKAYA", "GONBA"):
            assert name in out

    def test_header_states_retention_window(self):
        """Пустой last_used = «не ходил за окно хранения», а не «не ходил никогда»."""
        assert "90" in render(self._rows(), "table", 90)

    def test_inactive_key_is_visible_as_such(self):
        out = render(self._rows(), "md", 90)
        assert "НЕТ" in out

    def test_json_is_machine_readable_with_iso_dates(self):
        payload = json.loads(render(self._rows(), "json", 90))
        assert payload["retention_days"] == 90
        by_name = {k["name"]: k for k in payload["keys"]}
        assert by_name["VMALMYZHE"]["last_used"] == "2026-08-03T08:15:00"
        assert by_name["GONBA"]["created_at"] is None
        assert by_name["KAZANSKAYA"]["is_active"] is False
