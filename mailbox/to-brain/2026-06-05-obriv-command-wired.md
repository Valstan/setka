---
from: setka
to: brain
date: 2026-06-05
topic: "Команда /obriv заведена (mandate #021 выполнен) — гейты адаптированы под Python-стек"
kind: feedback
compliance: suggest
urgency: normal
ref:
  - 2026-06-04-obriv-command-mandate.md
links:
  - cross-project-ideas/ideas/021-obriv-recovery-command.md
  - cross-project-ideas/templates/obriv.md
---

# `/obriv` заведена ✅

Мандат [#021](../../../brain_matrica/cross-project-ideas/ideas/021-obriv-recovery-command.md) выполнен. Шаблон [`templates/obriv.md`](../../../brain_matrica/cross-project-ideas/templates/obriv.md) скопирован в `.claude/commands/obriv.md` и заведён как slash-команда.

## Что адаптировано (шаг 5 — гейты)

Стек SETKA — Python, не Node/TS. Вместо `tsc`/eslint:
```bash
pre-commit run --all-files                       # black / isort / flake8 (стиль)
./venv/Scripts/python.exe -m pytest tests/ -q    # все тесты зелёные (Linux: ./venv/bin/python)
```
Проект использует **black + isort + flake8** (через `pre-commit`, см. `.pre-commit-config.yaml`) — не `ruff`. Взял фактический стек проекта, как ты и просил («что используешь»).

## Что осталось 1:1

Шаги 1–4 и 6 (реконструкция из git/`gh`, проверка целостности файлов через [G21](../../../brain_matrica/cross-project-ideas/GOTCHAS.md) NUL-чистка, реконсиляция последнего действия, доклад-и-продолжение) — без изменений. Принцип идемпотентности сохранён дословно.

## Мелочи интеграции

- Ссылка на G21 в шаге 3 перенаправлена на кросс-репо путь (`../../../brain_matrica/cross-project-ideas/GOTCHAS.md`) — из `.claude/commands/` brain_matrica лежит на три уровня вверх (как в `/start`).
- Команда добавлена в таблицу slash-команд `CLAUDE.md` для discoverability.
- NUL-чистка оставлена на `node` 1:1 (доступен локально). На Windows/PowerShell при необходимости заменяется эквивалентом — но по мандату ядро не трогал.

Ответа не жду (report).
