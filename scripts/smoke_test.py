#!/usr/bin/env python3
"""Post-deploy smoke-test для SETKA: dry-run прогон пайплайна без публикации.

После рестарта сервисов (`/reliz` Шаг 8) полезно за один шаг убедиться, что
пайплайн региона жив: токены валидны, VK отвечает, парсинг → фильтр → сборка
сводки проходят. Возвращает exit-код, пригодный для автоматической проверки
в `/reliz`.

Механика: ставит ``parse_and_publish_theme(dry_run=True)`` **напрямую в Celery**
и опрашивает ``AsyncResult`` — тем же способом, что web-эндпоинт diagnostics
(``web/api/regions.py``), но минуя web-слой: diagnostics-эндпоинты живут под
операторской сессией, и у post-deploy скрипта нет cookie (старый HTTP-путь
падал на 401). Публикация и запись в БД при ``dry_run=True`` не происходят.

Использование (на проде, из venv проекта — скрипт импортирует
``tasks.celery_app``, поэтому чужим интерпретатором не запускается):
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/smoke_test.py"
    ssh setka "cd /home/valstan/SETKA && ./venv/bin/python scripts/smoke_test.py \\
        --region mi --theme novost --min-posts 1"

Exit 0 — smoke прошёл; exit 1 — провал (детали в stderr); exit 2 — ошибка
аргументов/постановки задачи. Чистая логика вынесена в ``evaluate_result``,
оркестрация ``run_smoke`` принимает инжектируемые ``submit``/``poll`` — юнит-тесты
не поднимают Celery.
"""

from __future__ import annotations

import argparse
import sys
import time
from typing import Any, Callable, Dict, List, Optional

DEFAULT_REGION = "mi"
DEFAULT_THEME = "novost"
DEFAULT_MIN_POSTS = 1
DEFAULT_TIMEOUT = 180  # сек на весь dry-run (реальный VK-парсинг бывает долгим)
DEFAULT_POLL_INTERVAL = 3  # сек между опросами статуса задачи

TASK_NAME = "tasks.parsing_scheduler_tasks.parse_and_publish_theme"


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


def submit_dry_run(region: str, theme: str) -> str:
    """Поставить dry-run задачу пайплайна в Celery, вернуть task_id.

    Импорты внутри: скрипт остаётся импортируемым без Celery (для юнит-тестов
    оркестрации с фейковыми ``submit``/``poll``)."""
    from tasks.celery_app import app as celery_app

    task = celery_app.send_task(
        TASK_NAME, kwargs={"region_code": region, "theme": theme, "dry_run": True}
    )
    return task.id


def poll_task(task_id: str) -> Dict[str, Any]:
    """Статус Celery-задачи — та же форма словаря, что web-эндпоинт
    ``/api/regions/diagnostics/task/{id}/status`` (см. web/api/regions.py)."""
    from celery.result import AsyncResult

    from tasks.celery_app import app as celery_app

    ar = AsyncResult(task_id, app=celery_app)
    state = ar.state
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "state": state,
        "ready": ar.ready(),
        "result": None,
        "error": None,
    }
    if state == "SUCCESS":
        try:
            payload["result"] = ar.result
        except Exception as e:  # pragma: no cover - тонкая обёртка backend'а
            payload["error"] = f"не удалось получить result: {e}"
    elif state == "FAILURE":
        try:
            payload["error"] = str(ar.result)
        except Exception:  # pragma: no cover
            payload["error"] = "задача завершилась с ошибкой"
    return payload


def run_smoke(
    region: str,
    theme: str,
    min_posts: int,
    timeout: float,
    poll_interval: float,
    *,
    submit: Callable[[str, str], str] = submit_dry_run,
    poll: Callable[[str], Dict[str, Any]] = poll_task,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.monotonic,
    log: Callable[[str], None] = lambda m: print(m, file=sys.stderr),
) -> int:
    """Оркестрация smoke-теста. Возвращает exit-код (0 OK / 1 fail / 2 ошибка).

    Коллабораторы (``submit``/``poll``/``sleep``/``now``/``log``) инжектируются
    для тестов.
    """
    log(f"[smoke] dry-run пайплайна region={region} theme={theme} (Celery напрямую)")

    try:
        task_id = submit(region, theme)
    except Exception as e:
        log(f"[smoke] FAIL: не удалось поставить dry-run задачу: {e}")
        return 2
    if not task_id:
        log("[smoke] FAIL: постановка задачи не вернула task_id")
        return 2
    log(f"[smoke] задача поставлена: task_id={task_id}, опрос до {timeout:.0f}s")

    deadline = now() + timeout
    last: Dict[str, Any] = {}
    while now() < deadline:
        try:
            last = poll(task_id)
        except Exception as e:  # pragma: no cover - transient backend
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
        f"would_publish={result.get('bulletins_count')}, "
        f"communities={result.get('communities_count')}"
    )
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--region", default=DEFAULT_REGION, help="код эталонного региона")
    parser.add_argument("--theme", default=DEFAULT_THEME, help="тема сводки")
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
        region=args.region,
        theme=args.theme,
        min_posts=args.min_posts,
        timeout=args.timeout,
        poll_interval=args.poll_interval,
    )


if __name__ == "__main__":
    sys.exit(main())
