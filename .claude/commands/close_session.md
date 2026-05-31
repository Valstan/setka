---
description: Закрыть сессию разработки SETKA (ЕДИНСТВЕННАЯ команда закрытия) — закоммитить и запушить ВСЁ на GitHub, обновить SESSION_HANDOFF.md и PENDING, через PR с авто-merge для doc-only. Триггерится фразами «закрой сессию», «закрой сессию разработки», «заверши сессию», «закрываемся».
argument-hint: (без аргументов | короткое описание нитки | --no-automerge)
allowed-tools: Read, Write, Edit, Bash, Glob, Grep, AskUserQuestion
---

# /close_session — закрыть сессию разработки SETKA

**Единственная команда закрытия сессии.** За один проход делает две вещи:

1. **Гарантирует, что вся работа на GitHub** — источник истины при работе на нескольких компьютерах (днём один, вечером другой). Коммитит и пушит все рабочие правки (код + доки) через PR-flow и проверяет жёстким гейтом, что дерево чистое и всё на `origin`.
2. **Фиксирует state of mind** в `docs/SESSION_HANDOFF.md` (текущая нитка, следующий шаг, failed approaches), чтобы следующая сессия (на этом или другом компе после `git pull`) мгновенно подхватила работу.

> Запускается и естественными фразами: **«закрой сессию»**, **«закрой сессию разработки»**, **«заверши сессию»**, **«закрываемся»** — все они означают эту команду.

**Что НЕ делает:** не деплоит на прод. Для деплоя — [`/reliz`](reliz.md) (PR → merge → SSH `git pull` → миграции → restart → health).

| Команда | Что делает |
|---|---|
| [`/close_session`](close_session.md) | Закрыть сессию: закоммитить+запушить **ВСЁ** (код + доки) через PR, обновить `SESSION_HANDOFF` + `PENDING`, проверить sync-гейт |
| [`/reliz`](reliz.md) | Деплой на прод: PR → merge → SSH `git pull` → миграции → `systemctl restart` → health |

> Команда `/finish` удалена 2026-05-30 — её роль «закоммитить рабочий код» теперь **внутри** `/close_session`. Одна команда закрытия — без путаницы.

## Когда вызывать

- Перед паузой в работе (конец дня), сменой компа, длительным перерывом.
- После значимого milestone (PR merged, нитка частично закрыта).
- **Цель:** после закрытия ничего не должно остаться только на этой машине.
- **НЕ вызывать** во время активного `/reliz` (сначала доделай релиз).

## Шаг 1. Контекст (read-only, один блок)

```bash
git status --short --branch
git rev-parse --abbrev-ref HEAD
git log --oneline -10
git log --oneline main..HEAD 2>/dev/null            # что в feature-ветке (если)
git log -1 --format='%H %s' -- docs/SESSION_HANDOFF.md
bash scripts/git_sync_check.sh --gate || true       # текущее состояние синхронизации
```

Параллельно:
- `Read` `docs/SESSION_HANDOFF.md` (если есть) — сравнить факт vs план начала сессии.
- `Read` `docs/PENDING_FOLLOWUPS.md` (первые 60 строк) — что синхронизировать.
- `TaskList` (если использовался) — состояние tasks сессии.

Если `git_sync_check.sh --gate` уже даёт **exit 0** (дерево чистое, всё запушено), последний handoff-коммит — сегодня, и в TaskList нет открытых задач — сказать «всё уже на GitHub и handoff свежий, закрывать нечего» и завершиться.

## Шаг 2. Sync-gate part A — закоммитить и запушить ВСЕ рабочие правки

Если `git status` непустой (есть незакоммиченные правки кода/доков):

1. **Если на `main`** — создать feature-ветку (direct push в main запрещён, [ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)):
   ```bash
   git checkout -b <type>/<slug>   # feat/ fix/ chore/ docs/ refactor/
   ```
2. **Описательный commit message** (Conventional Commits; хронология ведётся в git — [ADR-0001](../../docs/adr/0001-archive-dev-history.md)): subject ≤70 символов + тело (что меняли, почему, какие тесты, как применять на проде — миграция? restart? оба? ничего?).
   ```bash
   git add -A
   git commit -m "$(cat <<'EOF'
   <type>(scope): <subject под 70 символов>

   <тело: что меняли, почему, какие тесты прошли, как применять на проде.>

   Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
   EOF
   )"
   ```
3. `git push -u origin <branch>`.

Если кода значимо много / он сырой — спроси пользователя через `AskUserQuestion`, коммитить ли всё одним коммитом или он хочет разбить. Но **по умолчанию цель — ничего не оставить незапушенным**: push в feature-ветку снимает риск рассинхрона между машинами, даже если PR ещё не смержен.

Если рабочих правок нет — пропустить шаг.

## Шаг 3. Уточнить нитку (через `AskUserQuestion` — один блок, max 4 вопроса)

Если из коммитов сессии и TaskList всё ясно — **не спрашивай**, сразу пиши драфт handoff'а. Иначе:

1. **Какая нитка активна?** (1-3 предложения). Если из `$ARGUMENTS` уже понятно — пропустить.
2. **Status:** `ACTIVE` (есть нитка) или `IDLE` (всё закрыто)?
3. **Следующий шаг?** Конкретно: первое действие следующей сессии с file paths и командами.
4. **Failed approaches?** Только при `ACTIVE` — что пробовали и отвергли (с причиной).

При `IDLE` — failed approaches из старого handoff перенеси в commit-message закрывающей нитки или в ADR (если урок архитектурный), затем перезаписывай handoff пустыми секциями.

## Шаг 4. Записать `docs/SESSION_HANDOFF.md`

**Перезаписать целиком** через `Write` по шаблону (история — в `git log --follow -- docs/SESSION_HANDOFF.md`, не аккумулируй здесь):

```markdown
# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE | IDLE
**Updated:** YYYY-MM-DD
**Branch:** <git current branch>
**Last release in prod:** <последний коммит на проде или ->

---

## Текущая нитка

<1-3 предложения: что делаем, в какой стадии, почему длинная задача.>
<если IDLE — «_Нет — последняя задача закрыта, открытая стартовая позиция._»>

## Следующий шаг

<конкретно: первое действие новой сессии. С file paths и командами.>
<если IDLE — 2-3 кандидатные стартовые точки из PENDING_FOLLOWUPS.>

## Контекст

- **План:** <путь к docs/plans/X.md если есть, иначе «нет активного плана»>
- **Связанные коммиты сессии:** <hashes + одной строкой о каждом>
- **Прод:** <какие сервисы active, последний коммит на проде, расхождение с main>
- **Открытых PR:** <link или «нет»>

## Failed approaches (этой нитки)

<если пробовали и отвергли — фиксировать, чтобы будущая сессия не повторила:>
- **<подход X>** — попробовали в [<hash>](...). Не сработало: <причина>. **Не повторять** без новой инфы.
<если не было — «_Не было._»; если IDLE — секцию убрать.>

## Открытые вопросы для пользователя

<список или «_Нет._»>

## Не забыть (low-priority)

<точечно из PENDING_FOLLOWUPS или памяти сессии>

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
```

## Шаг 5. Синхронизировать `PENDING_FOLLOWUPS.md` (если нужно)

- **Закрыли** пункты — удалить строку или `~~...~~ закрыто в PR #N`.
- **Появились новые** техдолги/идеи — добавить с приоритетом 🔴⏳🟡🟢.
- **Изменился приоритет** — переставить.

Не дублировать содержимое handoff'а — handoff **ссылается** на пункты PENDING, не повторяет.

## Шаг 5.5. Шеринг находки в мозг (условный)

Родилась ли в этой сессии значимая **переносимая** находка — скилл / фича / паттерн / решённая нетривиальная боль? Прогони через фильтр (pool [#009 share-findings-reflex](../../../brain_matrica/cross-project-ideas/ideas/009-share-findings-reflex.md), [директива 2026-05-29](../../../brain_matrica/mailboxes/setka/from-brain/2026-05-29-share-findings-reflex.md), `recommend`). Слать **только если выполнены все три**:

1. **Значимость** — новый скилл/фича/паттерн/решённая нетривиальная боль, не рутина.
2. **Переносимость** — применимо за пределами домена setka (другой проект мог бы переиспользовать).
3. **Неочевидность** — без этой работы сам бы не знал; есть «урок».

Любое «нет» → **ничего не пиши** (тишина = норма; по умолчанию молчим). «Пофиксил парсер VK-ответа» — не проходит; письмо про секреты вне репо ([2026-05-28-secrets-outside-repo-pattern.md](../../mailbox/to-brain/2026-05-28-secrets-outside-repo-pattern.md)) — эталон, который проходит.

Если **да** — `Write` в **свой** репо `mailbox/to-brain/YYYY-MM-DD-<slug>.md` (формат как у эталона: frontmatter `from: setka`, `to: brain`, `kind: idea`, `compliance: suggest`). Файл уедет в тот же закрывающий PR — Шаг 6 добавляет `mailbox/to-brain/` в commit, Шаг 9 разрешает ему авто-merge. **Не писать в `../brain_matrica/`** — только свой репо ([ADR-0001 асимметрия](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md)).

## Шаг 6. Коммит handoff + push

- **Если уже на feature-ветке** (Шаг 2 создал её или работали на ней) — добавить handoff-коммит **в ту же ветку**, `git push`.
- **Если рабочих правок не было и мы на `main`** — создать `chore/handoff-YYYY-MM-DD`, коммит, push.

```bash
# mailbox/to-brain/ — если Шаг 5.5 написал письмо-находку; иначе no-op.
git add docs/SESSION_HANDOFF.md docs/PENDING_FOLLOWUPS.md mailbox/to-brain/
git commit -m "$(cat <<'EOF'
chore(session): handoff — <одна строка о нитке или «закрытие сессии IDLE»>

<2-3 строки: что сделано, какая нитка остаётся активной (или почему IDLE), что следующее.>

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
git push
```

## Шаг 7. PR

Если PR ещё нет — создать; если уже открыт (с Шага 2) — handoff-коммит уже в нём, ничего создавать не нужно.

```bash
gh pr create --title "<type>(scope): <тема>" --body "$(cat <<'EOF'
## Summary

<что меняли и почему; если handoff-only — «Handoff обновлён через /close_session».>
- **Status:** ACTIVE | IDLE
- **Текущая нитка:** ...
- **Следующий шаг:** ...

## Test plan

- [x] pre-commit run --all-files Passed (если код)
- [x] pytest tests/ -q — N/N (если код)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

## Шаг 8. Sync-gate part B — проверка «всё на GitHub» (жёсткий гейт)

```bash
bash scripts/git_sync_check.sh --gate
```

Должно вернуть **exit 0** (дерево чистое + всё запушено на origin). Если **exit 1** — **НЕ объявлять сессию закрытой**: разобраться, что осталось незакоммиченным/незапушенным, и дослать (вернуться к Шагу 2/6). Это гейт против рассинхрона между машинами — пройти его обязательно.

## Шаг 9. Авто-merge handoff-PR (только для doc-only PR)

Если PR — **только** про доки (изменены **только** `docs/SESSION_HANDOFF.md`, `docs/PENDING_FOLLOWUPS.md` и/или письмо-находка `mailbox/to-brain/*.md` с Шага 5.5), его можно авто-смёрджить после CI. Письма в `mailbox/to-brain/` — чистые доки (brain читает их read-only), кода не несут.

**Не делать авто-merge, если:**
- В PR есть **любые** файлы кроме `docs/SESSION_HANDOFF.md` / `docs/PENDING_FOLLOWUPS.md` / `mailbox/to-brain/*.md` (например, в ветке есть feature-коммиты с Шага 2 — это код, ему нужно ревью / `/reliz`).
- В PR-ветке несколько коммитов с кодом.
- `--no-automerge` в `$ARGUMENTS`.

### Алгоритм

```bash
PR_NUM=<номер открытого PR>
# Разрешены: два handoff-дока + любое письмо-находка под mailbox/to-brain/.
ALLOWED='docs/SESSION_HANDOFF.md docs/PENDING_FOLLOWUPS.md'
files=$(gh pr view "$PR_NUM" --json files --jq '.files[].path')
extra=$(echo "$files" \
  | grep -v -F -x -f <(echo "$ALLOWED" | tr ' ' '\n') \
  | grep -v '^mailbox/to-brain/.*\.md$' || true)
```

Если `extra` непустой — пропустить авто-merge, доложить: «PR содержит код — merge руками после ревью или через `/reliz`: `gh pr merge $PR_NUM --squash --delete-branch`».

Иначе дождаться CI и смёрджить:

```bash
gh pr checks "$PR_NUM" --watch --interval 15      # блокирует, пока checks не завершатся
# Если CI не запустился за 180s — не зацикливаться, сказать «merge руками» и выйти.
gh pr merge "$PR_NUM" --squash --delete-branch
git checkout main && git pull --ff-only origin main
```

**Не использовать `--admin`**, не amend/force-push, не запускать CI вручную.

## Шаг 10. Финальный отчёт

Структура (5-9 строк, на русском):

1. **Сделано в сессии:** 1-3 строки из git log.
2. **✅ Всё на GitHub:** ветка `<branch>` запушена, PR #N (смержен / ждёт ревью); `git_sync_check.sh --gate` → OK.
3. **Handoff:** Status, текущая нитка, следующий шаг.
4. **PENDING:** что закрыто / добавлено / переприоритезировано.
5. **Merge:** «авто-merge» / «ждёт ручного merge: `gh pr merge <N> --squash --delete-branch`» / «PR содержит код — merge через `/reliz`».
6. **Готово к закрытию терминала / смене компа** — ничего не осталось только локально.

Финальная строка — «До следующей сессии. `/start` сам подсветит нитку из handoff'а (и предупредит, если что-то не на GitHub).»

## Что НЕ делать в `/close_session`

- **Не push на прод** (`ssh setka`), не перезапускать сервисы, не применять миграции — это `/reliz`.
- **Не direct push в main** ([ADR-0002](../../../brain_matrica/adr/0002-pr-only-flow-no-direct-push.md)) — даже handoff идёт через PR.
- **Не `gh pr merge --admin`** — даже если CI залип; доложить, merge руками.
- **Не объявлять сессию закрытой, пока `git_sync_check.sh --gate` не вернёт exit 0.**
- **Не дублировать `PENDING_FOLLOWUPS` в handoff** — handoff ссылается, не повторяет.

---

> **Авто-архивация сессий:** Claude Desktop (вкладка **Cowork**) может авто-классифицировать сессии как «done» и убирать их в архив — это UI-настройка «Classify session states» (её **нет** в `settings.json`, отключается только в Cowork). Чтобы сессии не уходили в архив без твоего ведома — отключи её в Cowork. Независимо от этого SessionStart-хук (`scripts/git_sync_check.sh --warn`, прописан в `.claude/settings.json`) при входе в каждую сессию предупредит, если на машине осталась несинхронизированная с GitHub работа.
