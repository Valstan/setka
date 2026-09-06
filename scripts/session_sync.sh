#!/usr/bin/env bash
# Хук PostToolUse: опубликовать состояние сессии после мержа/пуша.
#
# Зачем отдельно от session_autosave.sh: локальный снимок пишется после каждого
# хода и стоит миллисекунды, а публикация трогает git и сеть — её незачем делать
# чаще, чем работа реально уезжает на GitHub.
#
# Хук зовётся на КАЖДУЮ команду агента, поэтому дешёвый фильтр стоит здесь, до
# запуска интерпретатора: старт Python — это ~100 мс, и на сотнях команд сессии
# он превратился бы в заметный налог на разработку. Питон видит только мержи и
# пуши, где тот же фильтр продублирован (`--if-merge`) и покрыт тестами.
#
# Никогда не падает и никогда не ждёт сеть: сам push уходит отдельным процессом.

set -u
cd "$(dirname "$0")/.." 2>/dev/null || exit 0

MODE="${1:-}"
PAYLOAD=""
if [ "$MODE" != "--always" ]; then
  # stdin может отсутствовать (ручной запуск) — тогда публиковать нечего.
  PAYLOAD=$(cat 2>/dev/null || true)
  printf '%s' "$PAYLOAD" | tr -d '\n' | grep -qiE 'gh pr merge|git push|git merge' || exit 0
fi

for candidate in \
  "venv/Scripts/python.exe" \
  "venv/bin/python" \
  "$(command -v python3 2>/dev/null || true)" \
  "$(command -v python 2>/dev/null || true)"; do
  [ -n "$candidate" ] && [ -x "$candidate" ] || continue
  if [ "$MODE" = "--always" ]; then
    # SessionEnd: сессия кончилась — публикуем без проверки «был ли мерж».
    "$candidate" scripts/session_autosave.py --sync --force --quiet >/dev/null 2>&1
  else
    printf '%s' "$PAYLOAD" \
      | "$candidate" scripts/session_autosave.py --sync --if-merge --force --quiet >/dev/null 2>&1
  fi
  exit 0
done

exit 0
