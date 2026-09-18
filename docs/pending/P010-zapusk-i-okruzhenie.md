# Запуск и окружение

> Запись `P010` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

- ~~**`main.py:25` хардкодит `~/SETKA/logs/app.log`.**~~ Закрыто ранее: `main.py:45` уже использует `os.getenv("LOG_PATH", "~/SETKA/logs/app.log")` с safe-fallback на StreamHandler. Запись была устаревшей.
- ~~**`venv` создаётся вручную в каждом worktree.**~~ Закрыто ранее: есть `scripts/setup-dev.ps1` и `scripts/setup-dev.sh`. 2026-05-22 добавлено `pre-commit install` в оба скрипта — теперь свежий worktree сразу получает git-хук.
- ~~**`scripts/setup-dev.ps1` хардкодит `py -3.11`**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): fallback `py -3.11 → -3.12 → -3` по аналогии с `setup-dev.sh`. Заодно добавлен UTF-8 BOM (PS 5.1 без BOM путал encoding на русской локали).
- ~~**Хардкоды `~/SETKA/logs/parser*` в `web/api/parsing.py` и `tasks/parsing_tasks.py`.**~~ Закрыто 2026-05-23 (см. `DEV_HISTORY.md`): введён env `SETKA_LOGS_DIR` (default `~/SETKA/logs`), `OUTPUT_DIR`/`REPORTS_DIR`/`VIDEO_REPORT_PATH` вычисляются от него, `_init_logger` safe-fallback'ит на StreamHandler при недоступном пути. В `web/api/parsing.py` удалён неиспользуемый дубль `OUTPUT_DIR`. +5 тестов в `tests/test_tasks/`.
