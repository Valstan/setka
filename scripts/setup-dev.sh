#!/usr/bin/env bash
# scripts/setup-dev.sh — first-time dev setup для SETKA на Linux/macOS.
#
# Что делает:
#   1. Проверяет что доступен python3.11 (на проде — 3.12; на dev обычно 3.11).
#   2. Создаёт venv в текущем каталоге, если его ещё нет.
#   3. Обновляет pip.
#   4. Устанавливает requirements.txt + pytest + pytest-asyncio.
#   5. Прогоняет pytest --collect-only как sanity-check.
#
# Запуск:
#   ./scripts/setup-dev.sh
#
# Идемпотентно. На проде НЕ запускать — там venv ставится по DEPLOY.md
# с python3.12, и эта помойка может перебить системную сборку.

set -euo pipefail

# Подбираем подходящий интерпретатор: 3.11 предпочтительнее (как в worktree'ах),
# 3.12 — fallback (как на проде).
PYTHON=""
for candidate in python3.11 python3.12 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [[ -z "$PYTHON" ]]; then
    echo "[setup-dev] ERROR: ни python3.11, ни python3.12, ни python3 не найдены в PATH" >&2
    exit 1
fi

PY_VER="$("$PYTHON" --version 2>&1)"
echo "[setup-dev] using $PYTHON ($PY_VER)"

if [[ ! -x "venv/bin/python" ]]; then
    echo "[setup-dev] creating venv …"
    "$PYTHON" -m venv venv
else
    echo "[setup-dev] venv already exists"
fi

echo "[setup-dev] upgrading pip …"
./venv/bin/python -m pip install --upgrade pip --quiet

echo "[setup-dev] installing requirements + test deps + pre-commit …"
./venv/bin/python -m pip install -r requirements.txt pytest pytest-asyncio pre-commit --quiet

echo "[setup-dev] editable install (pip install -e .) — for 'from modules.X import Y' …"
./venv/bin/python -m pip install -e . --quiet

if [[ -f ".pre-commit-config.yaml" ]]; then
    echo "[setup-dev] installing pre-commit git hook …"
    ./venv/bin/pre-commit install >/dev/null 2>&1 || true
fi

echo "[setup-dev] pytest --collect-only sanity-check …"
COLLECTED="$(./venv/bin/python -m pytest --collect-only -q 2>&1 | grep -E 'tests collected' || true)"
if [[ -z "$COLLECTED" ]]; then
    echo "[setup-dev] WARN: pytest --collect-only didn't print 'tests collected' — что-то не так"
else
    echo "[setup-dev] $COLLECTED"
fi

echo
echo "[setup-dev] DONE. Run tests:"
echo "    ./venv/bin/python -m pytest tests/ -q"
