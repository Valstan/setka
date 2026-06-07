#!/usr/bin/env python3
"""Дождаться готовности HTTP-эндпоинта после рестарта (poll, а не fixed-sleep).

Зачем. Прежний деплой-паттерн (`/reliz` Шаг 8, ad-hoc деплой-команды) делал
``restart && sleep 4 && curl --max-time 15`` — ОДИН выстрел после фикс-паузы.
На тонком VPS (1 ядро / 1.5 ГБ) при рестарте 3 сервисов разом uvicorn не успевал
подняться за паузу → curl получал ``000`` (connection refused) → ложный фейл
деплоя. Инцидент 2026-06-07: цикл деплоя 6× рестартил прод по кругу на ложном
000, хотя миграция/код применились с первого раза, а сервис был просто медленным
на старте. Поллинг убирает этот класс ложных фейлов (и риск откатить рабочий
деплой по ошибке).

Механика: GET ``--url`` каждые ``--interval`` сек, пока статус не станет
``--expect`` (по умолчанию 200) или не выйдет ``--timeout``. Любой не-2xx,
``000`` (нет соединения — сервис ещё стартует) → ретрай до дедлайна.

Использование (на проде, после рестарта):
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/wait_for_health.py"
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/wait_for_health.py \\
        --url http://127.0.0.1:8000/api/health/full --timeout 90 --interval 3"

Exit 0 — дождались ``expect``; exit 1 — таймаут (последний статус в stderr).
Stdlib-only (urllib), без зависимостей. Чистое ядро ``poll_health`` (инъекции
``check``/``sleep``/``now``/``log``) вынесено ради юнит-тестов без сети.
"""

from __future__ import annotations

import argparse
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

DEFAULT_URL = "http://127.0.0.1:8000/api/health/full"
DEFAULT_TIMEOUT = 90.0  # сек на весь старт (рестарт 3 сервисов на 1-ядерном VPS)
DEFAULT_INTERVAL = 3.0  # сек между опросами
DEFAULT_EXPECT = 200
DEFAULT_REQUEST_TIMEOUT = 10.0  # таймаут одного GET


def fetch_status(url: str, request_timeout: float = DEFAULT_REQUEST_TIMEOUT) -> int:
    """GET ``url`` → HTTP-код (int). ``0`` при ошибке соединения/таймауте.

    Тонкая сетевая обёртка (не покрывается юнит-тестами — мокается в ``poll_health``
    через инъекцию ``check``). ``0`` трактуем как «сервис ещё не слушает» (типично
    сразу после рестарта) — повод ретраить, а не падать.
    """
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=request_timeout) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as e:  # pragma: no cover - сетевой край
        return int(e.code)
    except Exception:  # pragma: no cover - connection refused / timeout / DNS
        return 0


def poll_health(
    check: Callable[[], int],
    *,
    timeout: float,
    interval: float,
    expect: int = DEFAULT_EXPECT,
    sleep: Callable[[float], Any] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    log: Callable[[str], Any] = lambda _m: None,
) -> Dict[str, Any]:
    """Опрашивать ``check()`` (→ HTTP-код) пока не вернёт ``expect`` или не выйдет таймаут.

    Чистая логика при инъекции ``check``/``sleep``/``now`` — тестируется без сети.
    Хотя бы одна попытка делается всегда (даже при ``timeout<=0``). Возвращает
    ``{ok, attempts, last_status, elapsed}``.
    """
    start = now()
    deadline = start + max(0.0, timeout)
    attempts = 0
    last: Optional[int] = None
    while True:
        attempts += 1
        last = check()
        t = now()
        if last == expect:
            return {"ok": True, "attempts": attempts, "last_status": last, "elapsed": t - start}
        if t >= deadline:
            return {"ok": False, "attempts": attempts, "last_status": last, "elapsed": t - start}
        log(f"health {last} != {expect}; ретрай через {interval}s (попытка {attempts})")
        sleep(interval)


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL, help="health-эндпоинт")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="общий таймаут, сек")
    parser.add_argument(
        "--interval", type=float, default=DEFAULT_INTERVAL, help="пауза между опросами, сек"
    )
    parser.add_argument("--expect", type=int, default=DEFAULT_EXPECT, help="ожидаемый HTTP-код")
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_REQUEST_TIMEOUT,
        help="таймаут одного GET, сек",
    )
    args = parser.parse_args(argv)

    res = poll_health(
        lambda: fetch_status(args.url, args.request_timeout),
        timeout=args.timeout,
        interval=args.interval,
        expect=args.expect,
        log=lambda m: print(m, file=sys.stderr),
    )

    if res["ok"]:
        print(
            f"health OK: {res['last_status']} за {res['attempts']} попыт., {res['elapsed']:.1f}s "
            f"({args.url})"
        )
        return 0
    print(
        f"health FAILED: последний={res['last_status']} за {res['attempts']} попыт., "
        f"{res['elapsed']:.1f}s (таймаут {args.timeout}s, {args.url})",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
