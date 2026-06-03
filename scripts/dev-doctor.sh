#!/usr/bin/env bash
# scripts/dev-doctor.sh — диагностика dev-окружения SETKA (read-only).
#
# Проверяет, что локальная машина готова разрабатывать SETKA: интерпретатор
# Python, venv, установленные зависимости, editable-install, pre-commit-хук,
# psql-клиент, git-синхронизацию и (best-effort) SSH-доступ к проду `setka`.
#
# Ничего НЕ меняет — только смотрит и докладывает. Для починки см. подсказки
# в выводе и scripts/setup-dev.sh.
#
# Запуск:
#   ./scripts/dev-doctor.sh            # полный прогон
#   ./scripts/dev-doctor.sh --no-prod  # пропустить SSH-probe прода
#
# Exit code: 0 — нет FAIL'ов (WARN допустимы), 1 — есть хотя бы один FAIL.

set -uo pipefail

# --- цвета (отключаются, если stdout не tty) ---
if [[ -t 1 ]]; then
    C_OK=$'\033[32m'; C_WARN=$'\033[33m'; C_FAIL=$'\033[31m'; C_DIM=$'\033[2m'; C_RST=$'\033[0m'
else
    C_OK=""; C_WARN=""; C_FAIL=""; C_DIM=""; C_RST=""
fi

FAILS=0
WARNS=0

ok()   { echo "  ${C_OK}✓${C_RST} $1"; }
warn() { echo "  ${C_WARN}!${C_RST} $1"; WARNS=$((WARNS + 1)); }
fail() { echo "  ${C_FAIL}✗${C_RST} $1"; FAILS=$((FAILS + 1)); }
hint() { echo "      ${C_DIM}↳ $1${C_RST}"; }
section() { echo; echo "── $1"; }

NO_PROD=0
for arg in "$@"; do
    [[ "$arg" == "--no-prod" ]] && NO_PROD=1
done

# Идём из корня репо независимо от того, откуда вызвали.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

echo "${C_DIM}SETKA dev-doctor — $REPO_ROOT${C_RST}"

# --- 1. Python интерпретаторы ---
section "Python"
FOUND_PY=""
for candidate in python3.11 python3.12 python3 py; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ver="$("$candidate" --version 2>&1)"
        ok "$candidate — $ver"
        [[ -z "$FOUND_PY" ]] && FOUND_PY="$candidate"
    fi
done
[[ -z "$FOUND_PY" ]] && fail "ни python3.11/3.12/3 не найдены в PATH" && hint "установи Python 3.11 (dev) или 3.12 (=прод)"

# --- 2. venv ---
section "venv"
VENV_PY=""
if [[ -x "venv/bin/python" ]]; then
    VENV_PY="venv/bin/python"
elif [[ -x "venv/Scripts/python.exe" ]]; then
    VENV_PY="venv/Scripts/python.exe"
fi
if [[ -n "$VENV_PY" ]]; then
    ok "venv найден — $("$VENV_PY" --version 2>&1)"
else
    fail "venv не найден в $REPO_ROOT/venv"
    hint "создать: ./scripts/setup-dev.sh (Linux/macOS) или scripts/setup-dev.ps1 (Windows)"
fi

# --- 3. Зависимости в venv ---
section "Зависимости"
if [[ -n "$VENV_PY" ]]; then
    check_mod() {
        local mod="$1" label="$2"
        if "$VENV_PY" -c "import $mod" >/dev/null 2>&1; then
            ok "$label"
        else
            fail "$label не импортируется"
            hint "./venv/bin/python -m pip install -r requirements.txt"
        fi
    }
    check_mod fastapi "fastapi (web)"
    check_mod celery "celery (worker/beat)"
    check_mod sqlalchemy "sqlalchemy (БД)"
    check_mod pytest "pytest"
    check_mod pytest_asyncio "pytest-asyncio"

    # editable install: пакет setka должен резолвиться без sys.path-хаков.
    if "$VENV_PY" -c "import modules" >/dev/null 2>&1; then
        ok "editable install ('import modules' работает)"
    else
        warn "'import modules' не работает — нет editable install"
        hint "./venv/bin/python -m pip install -e ."
    fi
else
    warn "пропуск — нет venv"
fi

# --- 4. pre-commit ---
section "pre-commit"
if [[ -f ".git/hooks/pre-commit" ]] && grep -q "pre-commit" ".git/hooks/pre-commit" 2>/dev/null; then
    ok "git-хук pre-commit установлен"
else
    warn "git-хук pre-commit не установлен — black/isort/flake8 не гоняются на commit"
    hint "./venv/bin/pre-commit install"
fi

# --- 5. psql-клиент ---
section "PostgreSQL-клиент"
if command -v psql >/dev/null 2>&1; then
    ok "psql — $(psql --version 2>&1)"
else
    warn "psql не найден (нужен только для ручных запросов к БД; на проде идём через ssh)"
fi

# --- 6. git-синхронизация ---
section "Git"
BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
ok "ветка: $BRANCH"
if [[ -n "$(git status --porcelain 2>/dev/null)" ]]; then
    warn "рабочее дерево не чистое (есть незакоммиченные изменения)"
fi
# Делегируем детальную проверку существующему git_sync_check.sh, если он есть.
if [[ -x "scripts/git_sync_check.sh" ]]; then
    if scripts/git_sync_check.sh --warn >/dev/null 2>&1; then
        ok "git_sync_check: всё синхронно с origin"
    else
        warn "git_sync_check: есть рассинхрон с origin — см. ./scripts/git_sync_check.sh --warn"
    fi
fi

# --- 7. SSH-доступ к проду (best-effort) ---
section "Прод (ssh setka)"
if [[ "$NO_PROD" == "1" ]]; then
    echo "  ${C_DIM}— пропущено (--no-prod)${C_RST}"
elif ! command -v ssh >/dev/null 2>&1; then
    warn "ssh не найден в PATH"
elif ! grep -qiE '^host[[:space:]]+setka([[:space:]]|$)' "$HOME/.ssh/config" 2>/dev/null; then
    warn "alias 'setka' не найден в ~/.ssh/config"
    hint "см. docs/REMOTE_ACCESS.md — прод-доступ только по SSH через alias setka"
else
    ok "alias 'setka' есть в ~/.ssh/config"
    if ssh -o ConnectTimeout=8 -o BatchMode=yes setka 'test -f /home/valstan/SETKA/main.py && echo OK' 2>/dev/null | grep -q OK; then
        ok "прод достижим, это SETKA (main.py на месте)"
    else
        warn "прод не ответил за 8с / BatchMode (норма, если нужен пароль/2FA или ты офлайн)"
    fi
fi

# --- Итог ---
section "Итог"
if [[ "$FAILS" -gt 0 ]]; then
    echo "  ${C_FAIL}FAIL: $FAILS${C_RST}, ${C_WARN}WARN: $WARNS${C_RST} — окружение не готово, см. ✗ выше."
    exit 1
elif [[ "$WARNS" -gt 0 ]]; then
    echo "  ${C_OK}OK${C_RST} (с замечаниями: ${C_WARN}WARN $WARNS${C_RST}) — разрабатывать можно."
    exit 0
else
    echo "  ${C_OK}Всё зелёное — окружение готово.${C_RST}"
    exit 0
fi
