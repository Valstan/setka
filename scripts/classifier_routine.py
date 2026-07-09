#!/usr/bin/env python3
"""Обвязка облачной рутины HITL-классификатора: HTTP-механика отдельно от разметки.

Рутина-агент в облаке раньше сама ходила к ``/api/classifier`` с ключом прямо в
тексте промпта — модель рутины стала отказываться (ключ в инструкции + автономные
HTTP-вызовы с ним читаются как небезопасный паттерн, см. Troubleshooting в
``docs/ops/hitl-classifier-routine.md``). Скрипт забирает всю обвязку в
детерминированный код: ключ — из env облачного окружения
(``CLASSIFIER_INGEST_KEY``), запросы — stdlib ``urllib`` (в облачном checkout'е
зависимости проекта не установлены — ничего не импортировать из проекта!).
Модели остаётся чистая редакторская задача: прочитать ``pending.json`` +
``postulates.md``, вынести вердикты, сохранить ``verdicts.json``.

Подкоманды:
  fetch  [--limit N] [--out DIR]   GET /postulates + /pending →
                                   DIR/postulates.md + DIR/pending.json,
                                   сводка JSON в stdout (DIR дефолт classifier_run)
  submit FILE                      локальная валидация + POST /verdicts из FILE
                                   ({"verdicts": [...]}), ответ сервера в stdout

Env: CLASSIFIER_INGEST_KEY (обязателен; секрет облачного окружения рутины),
CLASSIFIER_API_BASE (опционально; дефолт — ASCII-хост VPS, см. docs/GATEWAY.md).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# ASCII-хост VPS (punycode-IDN спотыкается об egress-прокси облака чаще).
DEFAULT_API_BASE = "https://3931b3fe50ab.vps.myjino.ru"
DEFAULT_OUT_DIR = "classifier_run"
DEFAULT_LIMIT = 40
TIMEOUT_S = 30
VALID_ACTIONS = ("publish", "delete", "hold")


def api_base() -> str:
    return os.environ.get("CLASSIFIER_API_BASE", DEFAULT_API_BASE).rstrip("/")


def _ingest_key() -> str:
    key = os.environ.get("CLASSIFIER_INGEST_KEY", "").strip()
    if not key:
        sys.exit(
            "CLASSIFIER_INGEST_KEY не задан: добавь его в переменные облачного "
            "окружения рутины (claude.ai/code → рутина → Update cloud environment)."
        )
    return key


def _request(method: str, path: str, body: dict | None = None) -> str:
    """HTTP к ingest-API; понятная смерть вместо трейсбека при сетевых бедах."""
    url = f"{api_base()}{path}"
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"X-API-Key": _ingest_key()}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        sys.exit(f"HTTP {exc.code} {method} {path}: {detail}")
    except urllib.error.URLError as exc:
        sys.exit(
            f"Сетевая ошибка {method} {url}: {exc.reason}. "
            "403 на CONNECT = egress-прокси окружения (Network access → Custom, "
            "см. Troubleshooting в docs/ops/hitl-classifier-routine.md)."
        )


def validate_verdicts(payload: object) -> list[str]:
    """Проверить пакет вердиктов ДО отправки; список ошибок (пустой = валидно).

    Ловим то, что модель чаще всего портит в JSON: нет lip, action вне словаря,
    confidence вне 0..100, merge_with не списком. Сервер валидирует ещё раз
    (pydantic) — тут ранняя внятная диагностика вместо HTTP 422.
    """
    if not isinstance(payload, dict) or not isinstance(payload.get("verdicts"), list):
        return ['ожидается объект {"verdicts": [...]}']
    items = payload["verdicts"]
    if not items:
        return ["пустой список verdicts — нечего отправлять"]
    errors: list[str] = []
    for i, v in enumerate(items):
        if not isinstance(v, dict):
            errors.append(f"[{i}] не объект")
            continue
        if not str(v.get("lip") or "").strip():
            errors.append(f"[{i}] нет lip")
        if v.get("action") not in VALID_ACTIONS:
            errors.append(f"[{i}] action={v.get('action')!r} не из {list(VALID_ACTIONS)}")
        conf = v.get("confidence")
        if conf is not None and not (
            isinstance(conf, (int, float)) and not isinstance(conf, bool) and 0 <= conf <= 100
        ):
            errors.append(f"[{i}] confidence={conf!r} вне 0..100")
        merge_with = v.get("merge_with")
        if merge_with is not None and not isinstance(merge_with, list):
            errors.append(f"[{i}] merge_with не список")
    return errors


def cmd_fetch(args: argparse.Namespace) -> None:
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    postulates = _request("GET", "/api/classifier/postulates")
    pending = json.loads(_request("GET", f"/api/classifier/pending?limit={args.limit}"))
    (out / "postulates.md").write_text(postulates, encoding="utf-8")
    (out / "pending.json").write_text(
        json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "count": pending.get("count", 0),
                "region_filter": pending.get("region_filter"),
                "out": str(out),
            },
            ensure_ascii=False,
        )
    )


def cmd_submit(args: argparse.Namespace) -> None:
    payload = json.loads(Path(args.file).read_text(encoding="utf-8"))
    errors = validate_verdicts(payload)
    if errors:
        sys.exit("Невалидные вердикты (исправь и повтори submit):\n" + "\n".join(errors))
    print(_request("POST", "/api/classifier/verdicts", body=payload))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_fetch = sub.add_parser("fetch", help="забрать постулаты + батч постов в файлы")
    p_fetch.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    p_fetch.add_argument("--out", default=DEFAULT_OUT_DIR)
    p_fetch.set_defaults(func=cmd_fetch)

    p_submit = sub.add_parser("submit", help="отправить verdicts.json на сервер")
    p_submit.add_argument("file")
    p_submit.set_defaults(func=cmd_submit)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
