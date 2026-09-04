#!/usr/bin/env bash
# SessionStart-хук: положить состояние репо в контекст сессии (D-066 §2, pool #268).
#
# Только чтение, без сети: git status/log локальной копии + sticky-note handoff.
# pull остаётся осознанным шагом /start — здесь мы лишь показываем, где стоим,
# чтобы сессия открывалась в теме, что бы владелец ни набрал первым.
# Никогда не валит старт: любая ошибка — молчаливый exit 0.

set -u
cd "$(dirname "$0")/.." 2>/dev/null || exit 0

echo "=== SETKA: состояние репо на старте сессии ==="
git status -sb 2>/dev/null | head -5
echo "--- последние коммиты"
git log --oneline -3 2>/dev/null
if [ -f docs/SESSION_HANDOFF.md ]; then
  echo "--- docs/SESSION_HANDOFF.md"
  cat docs/SESSION_HANDOFF.md
fi
exit 0
