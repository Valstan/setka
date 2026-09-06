#!/usr/bin/env bash
# Обёртка хука Stop: автоснимок сессии (scripts/session_autosave.py).
#
# Хук зовётся после каждого хода агента, поэтому обёртка обязана быть дешёвой и
# НИКОГДА не падать: ошибка здесь ломала бы ход, ради которого её ставили.
# Интерпретатор ищем сами — venv на Windows и Linux лежит по разным путям, а
# системный python может быть только `python3` (или вовсе отсутствовать).

set -u
cd "$(dirname "$0")/.." 2>/dev/null || exit 0

for candidate in \
  "venv/Scripts/python.exe" \
  "venv/bin/python" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  "$candidate" scripts/session_autosave.py "$@" >/dev/null 2>&1
  exit 0
done

exit 0
