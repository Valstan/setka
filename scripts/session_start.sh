#!/usr/bin/env bash
# SessionStart-хук: положить состояние репо в контекст сессии (D-066 §2, pool #268).
#
# Только чтение, без сети: git status/log локальной копии + sticky-note handoff.
# pull остаётся осознанным шагом /start — здесь мы лишь показываем, где стоим,
# чтобы сессия открывалась в теме, что бы владелец ни набрал первым.
# Никогда не валит старт: любая ошибка — молчаливый exit 0.
#
# С 06.09 печатает ещё и автоснимок прошлой сессии (`scripts/session_autosave.py`,
# хук Stop) — но ТОЛЬКО когда он свежее курируемого handoff'а, то есть прошлую
# сессию закрыли молча, не вызвав /close_session. Если handoff свежий, он и есть
# правда о нитке, а машинная выжимка была бы шумом в контексте.

set -u
cd "$(dirname "$0")/.." 2>/dev/null || exit 0

echo "=== SETKA: состояние репо на старте сессии ==="
git status -sb 2>/dev/null | head -5
echo "--- последние коммиты"
git log --oneline -3 2>/dev/null

HANDOFF="docs/SESSION_HANDOFF.md"
AUTOSAVE=".claude/session-state/latest.md"

if [ -f "$HANDOFF" ]; then
  echo "--- docs/SESSION_HANDOFF.md"
  cat "$HANDOFF"
fi

if [ -f "$AUTOSAVE" ]; then
  if [ ! -f "$HANDOFF" ] || [ "$AUTOSAVE" -nt "$HANDOFF" ]; then
    echo "--- ⚠️ Прошлую сессию закрыли без /close_session: автоснимок свежее handoff'а"
    cat "$AUTOSAVE"
  else
    echo "--- автоснимок прошлой сессии есть ($AUTOSAVE), но handoff свежее — не печатаю"
  fi
fi

exit 0
