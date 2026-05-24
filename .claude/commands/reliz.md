---
description: Релиз SETKA на прод — DEV_HISTORY+PENDING → commit → push → SSH git pull → миграции (если есть) → restart → проверки.
argument-hint: [короткое описание релиза, опционально]
allowed-tools: Read, Edit, Write, Bash, Glob, Grep, AskUserQuestion, mcp__ccd_session__mark_chapter
---

# /reliz — релиз правок на прод SETKA

Ведёт через все шаги один за другим. На каждом значимом шаге останавливается и проверяет с пользователем. Прод-операции через SSH (см. `docs/REMOTE_ACCESS.md`).

## Шаг 0. Pre-flight check

Параллельно:

```bash
git status --short --branch                 # что меняется
git diff --stat HEAD                        # объём
git log --oneline main..HEAD 2>/dev/null    # что в текущей ветке, если не main
git log --oneline -5                        # последние коммиты
```

Если рабочее дерево чистое и нет несмердженых коммитов в feature-ветке — сказать «коммитить нечего», выйти.

## Шаг 1. Качественные ворота

`AskUserQuestion`: «Прогнать тесты и pre-commit перед коммитом?» Опции:
- «Да, всё» — pytest + pre-commit
- «Только pytest»
- «Только pre-commit»
- «Пропустить» (если правка тривиальная: docs / комментарии)

Соответственно:

```bash
.\venv\Scripts\python.exe -m pytest tests/ -q 2>&1 | tail -15
pre-commit run --all-files 2>&1 | tail -30
```

(на Linux/worktree — `./venv/bin/python` соответственно).

Если что-то падает — стоп, показать вывод, спросить как поступить. **Не использовать** `--no-verify` / `pytest -k` для обхода без явного запроса пользователя.

## Шаг 2. Обновить `DEV_HISTORY.md` и `PENDING_FOLLOWUPS.md`

**Это критично — не пропускать.** Пользователь специально это просил (см. memory `feedback-commit-devhistory`).

1. `Read` `docs/DEV_HISTORY.md` (первые 100 строк).
2. Если за сегодняшнюю дату блока ещё нет — `Edit` добавить новый блок **сверху** (после шаблона/шапки) по формату:

```markdown
## YYYY-MM-DD — <Короткий заголовок>

**Тема сессии:** один абзац контекста.

### Изменения

- **`path/to/file.py`** — что и зачем.
- ...

### Проверка / прогон

- Локально: `pytest tests/ -q` — N/N зелёных.
- На проде: применено через `/reliz`.

### Хвосты, оставленные в `PENDING_FOLLOWUPS.md`

- 🟡 ...
```

3. `Read` `docs/PENDING_FOLLOWUPS.md`. Если что-то из закрываемой задачи висело в ⏳/🟡/🟢 — `Edit` убрать (или перенести в `DEV_HISTORY.md` как «закрыто в этой сессии»). Если в процессе вылезли новые техдолги — `Edit` добавить.

Делать это **до** коммита, чтобы попало в тот же коммит.

## Шаг 3. Commit + ветка

**PR-only flow** — direct push в `main` запрещён ([ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md), [POSTULATES §VI](../../../brain_matrica/docs/POSTULATES.md)). Исключение — hot-fix аварии прода (см. ниже §Hot-fix).

Если сейчас на `main` — создать feature-ветку **до коммита**:

```bash
# Slug — kebab-case, описательный. Префикс — по сути правки.
git checkout -b <type>/<slug>   # feat/, fix/, chore/, docs/, refactor/
```

`AskUserQuestion` — попросить короткое сообщение коммита (или предложить своё на основе `git diff --stat`). Conventional-commits prefix:

- `feat(scope):` — новая фича
- `fix(scope):` — баг-фикс
- `refactor(scope):` — рефакторинг без смены поведения
- `docs:` — только документация
- `chore:` — обслуживание (deps, configs)
- `test:` — только тесты

```bash
# Конкретные пути, НЕ git add -A
git add docs/DEV_HISTORY.md docs/PENDING_FOLLOWUPS.md <other-paths>

git commit -m "$(cat <<'EOF'
feat(scope): краткое описание

Опционально — тело с подробностями (что и почему).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

Покажи пользователю `git log -1 --stat` для подтверждения.

## Шаг 4. Push + PR

```bash
git push -u origin <type>/<slug>

gh pr create --title "<short subject, под 70 символов>" --body "$(cat <<'EOF'
## Summary
- bullet 1
- bullet 2

## Test plan
- [ ] pytest tests/ -q — N/N зелёных
- [ ] pre-commit run --all-files (если правка кода)
- [ ] /check skill после merge

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Покажи пользователю URL созданного PR и `gh pr diff` для финального review. **Без явного OK пользователя на diff — не мержить.**

После OK:

```bash
gh pr merge --squash --delete-branch
git checkout main && git pull --ff-only
```

Merge-стратегия по умолчанию `--squash` (для коротких PR в 1-3 коммита). Длинные линейки коммитов, где история ценна — `--merge` вместо `--squash` (спросить пользователя).

### Hot-fix исключение

Прод упал, нужно зафиксить в течение часа? Допустим direct push в `main` ([ADR-0002 §8](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)), **но обязательный follow-up PR постфактум** с описанием инцидента (для audit trail). Спросить пользователя через `AskUserQuestion`: «Это hot-fix аварии прода? (иначе — через PR)».

`AskUserQuestion`: «Продолжаем деплой на прод сейчас?» — варианты:
- «Да, выкатываем»
- «Стоп, посижу подумаю» — выйти; пользователь продолжит позже вручную или через повтор `/reliz`

## Шаг 5. Прод-доступ — подтверждение

`AskUserQuestion`: «Открыть SSH-доступ к `setka` на этот деплой?» — нужно один раз для всех последующих ssh-команд в этом флоу.

## Шаг 6. Прод: pull кода

```bash
ssh setka "cd /home/valstan/SETKA && git fetch --all && git log --oneline HEAD..origin/main 2>&1 | head -10"
```

Показать пользователю diff. Если есть конфликты или нет fast-forward — стоп, разобраться вручную.

Если всё чисто:

```bash
ssh setka "cd /home/valstan/SETKA && git pull --ff-only origin main && git log --oneline -3"
```

## Шаг 7. Миграции БД (если есть)

Проверить, есть ли в pushed-коммитах новые SQL-миграции:

```bash
git diff --name-only HEAD~1 HEAD -- 'database/migrations/*.sql' 2>&1
# или, если несколько коммитов:
git log --since=<previous-prod-commit> --name-only --diff-filter=A -- 'database/migrations/*.sql'
```

Если есть — для каждой:

1. `Read` файл.
2. `AskUserQuestion`: «Применить миграцию <NNN_file.sql> на прод?» с опциями «да / dry-run / отмена».
3. При «да» — через `/sql migrate <file>` или эквивалентно:
   ```bash
   ssh setka 'sudo -u postgres psql -d setka -f /home/valstan/SETKA/database/migrations/<file>'
   ```
4. Запомнить факт применения для `DEV_HISTORY` (если ещё не указали).

Если в pull притянулся `requirements.txt` — тогда:

```bash
ssh setka "cd /home/valstan/SETKA && source venv/bin/activate && pip install -r requirements.txt 2>&1 | tail -10"
```

Если в pull притянулся `pyproject.toml` (либо это первый деплой с editable install после 2026-05-24, либо `pyproject.toml` изменён — посмотри `git diff --name-only HEAD~1 HEAD -- pyproject.toml`) — переустановить editable пакет:

```bash
ssh setka "cd /home/valstan/SETKA && source venv/bin/activate && pip install -e . 2>&1 | tail -5"
```

Это регистрирует `setka` как editable-пакет в venv, чтобы `from modules.X import Y` работало из любой папки без `sys.path.insert` (см. `DEV_HISTORY.md` 2026-05-24 «pyproject.toml + editable install»). Прод-systemd-сервисы продолжают использовать `PYTHONPATH=/home/valstan/SETKA`, ничего там менять не нужно.

## Шаг 8. Restart сервисов

`AskUserQuestion`: «Перезапускаем `setka setka-celery-worker setka-celery-beat`?» — варианты:
- «Да, всё три»
- «Только setka» (если правка только в FastAPI)
- «Только celery-worker» (если правка только в tasks)
- «Никаких рестартов» (например, если изменены только тесты/доки)

Если «да»:

```bash
ssh setka "sudo systemctl restart <services> && sleep 4 && systemctl is-active <services>"
```

## Шаг 9. Проверки

Параллельно:

```bash
ssh setka "curl -s -o /dev/null -w '/api/health/full: %{http_code} in %{time_total}s\n' --max-time 15 http://127.0.0.1:8000/api/health/full"

ssh setka "systemctl is-active setka setka-celery-worker setka-celery-beat"

ssh setka "journalctl -u setka -u setka-celery-worker -u setka-celery-beat --since '2 minutes ago' --no-pager 2>&1 | grep -iE 'error|critical|exception' | tail -10"

ssh setka "tail -20 /home/valstan/SETKA/logs/app.log 2>&1 | grep -iE 'error|critical|exception' | tail -5"
```

Через внешний домен (опционально):

```bash
curl -s -o /dev/null -w 'public /: %{http_code}\n' --max-time 20 http://3931b3fe50ab.vps.myjino.ru/
```

## Шаг 10. Финальный отчёт

- Что коммитнули (`git log -1 --stat`)
- Что задеплоено (на проде новый коммит `<hash> <subject>`)
- Какие миграции применены (если были)
- Какие сервисы перезапущены
- Результаты health-проверок
- Если в `PENDING_FOLLOWUPS.md` остались хвосты — напомнить какие

## Если что-то упало

- **Тесты упали** → стоп до коммита, разобраться. **Никогда не** обходить через `--no-verify`.
- **psql упал на миграции** → откатить если можно (`BEGIN; ... ROLLBACK;` либо обратная миграция). Зафиксировать в `PENDING_FOLLOWUPS.md` как 🔴.
- **Сервис не запускается после restart** → `journalctl -u <service> -n 100 --no-pager`. Чаще всего — синтакс/импорт ошибка от свежего коммита. Откатить prod-репо: `ssh setka "cd /home/valstan/SETKA && git reset --hard <prev-hash>"` + restart. **Только с явным «да» пользователя через AskUserQuestion.**
- **`/api/health/full` отвечает 500** → тоже самое: журнал, откат.

Никогда не оставляй прод в сломанном виде. Если не можешь починить за 5 минут — спроси «откатываемся?», и при «да» выполни откат.
