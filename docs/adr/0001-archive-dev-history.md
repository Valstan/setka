# ADR-0001: Архивирована `docs/DEV_HISTORY.md` — переход на минималистичный AI-docs паттерн 2026

**Date:** 2026-05-24
**Status:** Accepted
**Drives:** [brain_matrica/cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md](../../../brain_matrica/cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md)

## Контекст

`docs/DEV_HISTORY.md` существовал в setka с эры переноса логики из Postopus (2024-2025). Файл хранил хронологию изменений: дата → блок про сессию (тема, изменения по файлам, тесты, применение, хвосты в `PENDING_FOLLOWUPS`).

К 2026-05-24 в файле накопилось ~30 записей за ~3 месяца активной разработки. Каждая запись дублирует информацию, которая уже живёт в:

- **Git commit messages** (Conventional Commits — `feat:` / `fix:` / `refactor:` с телом-описанием).
- **PR descriptions** (`gh pr view <n>` — Summary + Test plan).
- **`docs/SESSION_HANDOFF.md`** — sticky-note с активной ниткой, *Failed approaches* секцией (введён PR #20, 2026-05-23).
- **`docs/PENDING_FOLLOWUPS.md`** — открытые задачи с приоритетами.
- **GitHub releases / tags** (когда нужны).

## Решение

`docs/DEV_HISTORY.md` упраздняется. История записей сохранена в git: `git log --follow -- docs/DEV_HISTORY.md` показывает всё в виде diff'ов; финальная версия — на коммите `<этого PR>~1`.

## Что заменяет DEV_HISTORY

| Что было в DEV_HISTORY | Где живёт без неё |
|---|---|
| «Что сделано в каждой итерации» | `git log --oneline -20` с описательными commit messages (Conventional Commits) |
| «Контекст релиза» | `gh pr view <N>` (PR Summary) + GitHub releases когда нужны |
| «Уроки извлечённые / что попробовали и отбросили» | **Failed approaches** секция в `docs/SESSION_HANDOFF.md` (см. шаблон в `.claude/commands/close_session.md`) |
| «Куда мы шли» | `docs/SESSION_HANDOFF.md` (нить между сессиями) |
| «Архитектурные решения» | `docs/adr/` ADRs (этот файл — первый) |
| «Открытые задачи» | `docs/PENDING_FOLLOWUPS.md` |
| «Что задеплоили на прод» | `gh pr list --state merged --search 'in:title release'` + `ssh setka 'cd /home/valstan/SETKA && git log --oneline -5'` |

## Что **не** становится избыточным

- **Failed approaches** — уроки которые не попадают в коммиты (там только успехи). Должны где-то жить → секция в `SESSION_HANDOFF.md`.
- **ADR** — архитектурные решения с «почему именно так», полезны независимо от мощности AI.
- **PENDING_FOLLOWUPS** — открытые задачи, чтобы не забыть.

## Почему сейчас можно (а 2 года назад — нельзя)

1. **Context window** — Claude / GPT-5 могут читать `git log --since='3 months ago'` без проблем; раньше — нет.
2. **Sub-agents** — динамически вычитывают git/file-state, не нужен «pre-baked» сводный документ.
3. **Семантический поиск** — `gh search`, `gh pr list --search`, MCP-серверы дают живые данные.
4. **Лучшие commit messages** — Conventional Commits + AI-suggested messages = коммиты сами по себе informative.

## Что НЕ удалено

- Файл сохранён в `git log` — `git show HEAD~1:docs/DEV_HISTORY.md` или `git log --follow -- docs/DEV_HISTORY.md`.
- Исторические упоминания в комментариях кода (`utils/text_utils.py`, `modules/scheduler/__init__.py`, `database/models.py` и др.) и в SQL-миграциях (`database/migrations/*.sql`) оставлены как есть — они исторические markers, не активные ссылки.
- Исторические письма в `mailbox/to-brain/` с упоминаниями DEV_HISTORY не правятся — они immutable acknowledge'и предыдущих директив.

## Связано

- [brain pool #004 — Минимализм AI-docs 2026](../../../brain_matrica/cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md)
- [brain pool #003 — SESSION_HANDOFF + /close_session](../../../brain_matrica/cross-project-ideas/ideas/003-session-handoff.md) (фундамент)
- PR #20 от 2026-05-23 — введение `docs/SESSION_HANDOFF.md` и `/close_session` в setka.
- `.claude/commands/close_session.md` — шаблон handoff с Failed approaches секцией.

## Применимость

Это решение setka-specific. Братские проекты (Gonba `docs/DEVELOPMENT_LOG.md`, MatricaRMZ `docs/DEVELOPMENT_LOG.md`) — на момент 2026-05-24 ещё не упразднили свои аналоги. Их решение — за командой братьев, не за setka.
