#!/usr/bin/env python3
"""Post-deploy smoke-test для SETKA: dry-run прогон пайплайна без публикации.

После рестарта сервисов (`/reliz` Шаг 8) полезно за один шаг убедиться, что
пайплайн региона жив: токены валидны, VK отвечает, парсинг → фильтр → сборка
дайджеста проходят. Старый ручной способ — открыть `/regions/<code>/diagnostics`
в браузере. Этот скрипт делает то же из CLI и возвращает exit-код, пригодный
для автоматической проверки в `/reliz`.

Механика (переиспользует seam из PR #122):
  1. ``POST /api/regions/{region}/diagnostics?theme={theme}`` ставит Celery-задачу
     ``parse_and_publish_theme(dry_run=True)`` и возвращает ``task_id``.
  2. ``GET /api/regions/diagnostics/task/{task_id}/status`` опрашивается до
     ``ready`` (или таймаута).
  3. Результат (dry_run-словарь) проверяется ``evaluate_result``: ``success`` +
     спарсилось не меньше ``--min-posts`` постов. Публикация и запись в БД при
     ``dry_run=True`` не происходят (см. ``parse_and_publish_theme``).

Использование (на проде, локальный API):
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/smoke_test.py"
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/smoke_test.py \\
        --region mi --theme novost --min-posts 1"

Exit 0 — smoke прошёл; exit 1 — провал (детали в stderr); exit 2 — ошибка
аргументов/сети. Stdlib-only (urllib), чтобы запускаться где угодно без
зависимостей. Чистая логика вынесена в ``evaluate_result`` ради юнит-тестов.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
DEFAULT_REGION = "mi"
DEFAULT_THEME = "novost"
DEFAULT_MIN_POSTS = 1
DEFAULT_TIMEOUT = 180  # сек на весь dry-run (реальный VK-парсинг бывает долгим)
DEFAULT_POLL_INTERVAL = 3  # сек между опросами статуса задачи


def evaluate_result(result: Optional[Dict[str, Any]], min_posts: int) -> List[str]:
    """Проверить dry_run-результат. Возвращает список провалов (пусто = OK).

    Чистая функция (без сети) — вся диагностика smoke-теста тут, чтобы её можно
    было покрыть юнит-тестами без поднятия API/Celery.
    """
    failures: List[str] = []
    if result is None:
        return ["задача вернула пустой результат (result=None)"]

    if not result.get("success"):
        reason = result.get("error") or result.get("message") or "success=False без причины"
        failures.append(f"пайплайн вернул неуспех: {reason}")
        # Нет смысла проверять посты, если сам прогон провалился.
        return failures

    # ``posts_parsed`` присутствует только у настоящего dry_run-словаря. Ранние
    # success-возвраты («нет communities для темы») его не содержат — трактуем
    # как 0: для эталонного региона это тоже провал smoke (нечего парсить).
    posts_parsed = result.get("posts_parsed")
    if posts_parsed is None:
        if min_posts > 0:
            msg = result.get("message", "посты не парсились (нет dry_run-данных)")
            failures.append(f"не выполнен парсинг постов: {msg}")
    elif posts_parsed < min_posts:
        failures.append(f"спарсилось постов: {posts_parsed} < ожидаемого минимума {min_posts}")

    return failures


def _http_json(method: str, url: str, timeout: float) -> Dict[str, Any]:
    """Минималистичный JSON-запрос на stdlib. Бросает на сетевых/HTTP-ошибках."""
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8")
    return json.loads(raw) if raw else {}


def run_smoke(
    base_url: str,
    region: str,
    theme: str,
    min_posts: int,
    timeout: float,
    poll_interval: float,
    *,
    http: Callable[[str, str, float], Dict[str, Any]] = _http_json,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> int:
    """Оркестрация smoke-теста. Возвращает exit-код (0 OK / 1 fail / 2 ошибка).

    Коллабораторы (``http``/``sleep``/``now``/``log``) инжектируются для тестов.
    """
    base = base_url.rstrip("/")
    trigger_url = f"{base}/api/regions/{region}/diagnostics?theme={theme}"
    log(f"[smoke] dry-run пайплайна region={region} theme={theme} → {trigger_url}")

    try:
        trigger = http("POST", trigger_url, min(timeout, 30))
    except urllib.error.HTTPError as e:  # pragma: no cover - тонкая сетевая обёртка
        log(f"[smoke] FAIL: запуск diagnostics вернул HTTP {e.code}: {e.reason}")
        return 2
    except Exception as e:  # pragma: no cover
        log(f"[smoke] FAIL: не удалось запустить diagnostics: {e}")
        return 2

    task_id = trigger.get("task_id")
    if not task_id:
        log(f"[smoke] FAIL: diagnostics не вернул task_id: {trigger}")
        return 2
    log(f"[smoke] задача поставлена: task_id={task_id}, опрос до {timeout:.0f}s")

    status_url = f"{base}/api/regions/diagnostics/task/{task_id}/status"
    deadline = now() + timeout
    last: Dict[str, Any] = {}
    while now() < deadline:
        try:
            last = http("GET", status_url, min(timeout, 30))
        except Exception as e:  # pragma: no cover
            log(f"[smoke] предупреждение: опрос статуса упал ({e}), повтор…")
            sleep(poll_interval)
            continue

        if last.get("ready"):
            break
        sleep(poll_interval)
    else:
        log(f"[smoke] FAIL: задача не завершилась за {timeout:.0f}s (state={last.get('state')})")
        return 1

    if last.get("state") == "FAILURE":
        log(f"[smoke] FAIL: задача упала: {last.get('error')}")
        return 1

    failures = evaluate_result(last.get("result"), min_posts)
    if failures:
        for f in failures:
            log(f"[smoke] FAIL: {f}")
        return 1

    result = last.get("result") or {}
    log(
        "[smoke] OK: "
        f"posts_parsed={result.get('posts_parsed')}, "
        f"would_publish={result.get('digests_count')}, "
        f"communities={result.get('communities_count')}"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--base-url", default=DEFAULT_BASE_URL, help="API base (default prod local)"
    )
    parser.add_argument("--region", default=DEFAULT_REGION, help="код эталонного региона")
    parser.add_argument("--theme", default=DEFAULT_THEME, help="тема дайджеста")
    parser.add_argument(
        "--min-posts",
        type=int,
        default=DEFAULT_MIN_POSTS,
        help="минимум спарсенных постов (0 — проверять только success)",
    )
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="таймаут, сек")
    parser.add_argument(
        "--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL, help="интервал опроса, сек"
    )
    args = parser.parse_args(argv)

    return run_smoke(
        base_url=args.base_url,
        region=args.region,
        theme=args.theme,
        min_posts=args.min_posts,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    sys.exit(main())
