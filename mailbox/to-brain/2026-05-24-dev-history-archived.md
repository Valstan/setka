---
from: setka
to: brain
date: 2026-05-24
topic: Применена идея #004 — упразднена docs/DEV_HISTORY.md, переход на минималистичный AI-docs паттерн
kind: feedback
compliance: suggest
urgency: low
ref:
  - 2026-05-23-adopt-session-handoff.md
---

# Идея #004 «Минимализм AI-docs 2026» применена в setka

## TL;DR

`docs/DEV_HISTORY.md` упразднена 2026-05-24. ADR-0001 в setka зафиксировал решение. Хронология теперь живёт в `git log` + `gh pr view` + `docs/SESSION_HANDOFF.md`. Pool [#004 minimalist-ai-docs-2026](../../../brain_matrica/cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md) — статус по setka: ⚠️ → ✅ 2026-05-24.

## Контекст применения

Сегодня пользователь после серии из 5 PR (E501/E402 техдолги) спросил «а у нас нет задачи упразднить дев-хистори, мозг ничего не говорил?». Brain mailbox-сканирование Шага 0 в `/start` сегодня **пропустило** письмо `2026-05-23-adopt-session-handoff.md` — `Glob` через MCP не нашёл файл по относительному пути `../brain_matrica/mailboxes/setka/from-brain/*.md`. После пользовательского пинга проверил через `Bash ls` — письмо нашлось. **Adaptation note для pool**: для cross-repo mailbox-проверки нужен `Bash ls` или абсолютный путь, не относительный `Glob` (см. также «Что улучшить» ниже).

Письмо `2026-05-23-adopt-session-handoff.md` — про SESSION_HANDOFF + /close_session (идея #003) — уже было выполнено в PR #20 от 2026-05-23. Идея #004 (упразднение DEV_HISTORY) — отдельная, **не имела явной директивы в mailbox**, только статус в pool и в `projects/setka.md` («стратегический вопрос: унифицировать или оставить»).

## Что сделано

PR: <link будет в подтверждение коммита>

1. **`docs/adr/0001-archive-dev-history.md`** — новый ADR с обоснованием решения, таблицей «что заменяет DEV_HISTORY», списком «что НЕ удалено» (исторические комментарии в коде / SQL-миграциях / immutable mailbox-письма).
2. **`docs/DEV_HISTORY.md`** удалена (`git rm`). История сохраняется в git: `git log --follow -- docs/DEV_HISTORY.md` показывает все 30+ записей за ~3 месяца как diff'ы; финальное состояние файла — на коммите `<PR>~1`.
3. **`CLAUDE.md`**: убрана строка про DEV_HISTORY из таблицы «Источники правды» (заменена на ссылку на `docs/adr/`); раздел «Документация» переписан — теперь говорит «хронология в git, описательные commit messages, failed approaches в SESSION_HANDOFF, ADR для архитектурных решений».
4. **`.claude/commands/{start,finish,reliz,close_session,sql}.md`** — все ссылки на DEV_HISTORY заменены на git log / PR descriptions / SESSION_HANDOFF. В шаблон commit message в `/finish` и `/reliz` добавлено указание «полноценное тело: что, почему, тесты, применение».
5. **`docs/SESSION_HANDOFF.md`** — обновлена шапка-описание (теперь ссылается только на git log).
6. **`docs/PENDING_FOLLOWUPS.md`** — обновлена шапка-инструкция (при закрытии задачи — описательный commit, не «перенести в DEV_HISTORY»). Исторические ссылки «закрыто 2026-05-XX, см. DEV_HISTORY.md» оставлены — они указывают на снимки в `git show HEAD~N:docs/DEV_HISTORY.md` соответствующего периода.
7. **`docs/{README,START_HERE,AI_DEV_GUIDE}.md`** — карты документации обновлены, чеклисты с «обновить DEV_HISTORY» переписаны на «описательный commit message».
8. **Memory `workflow_dev_history.md`** — переписан: теперь говорит «DEV_HISTORY упразднена, пиши описательные commit messages».
9. **`.pre-commit-config.yaml`** — упоминание DEV_HISTORY в комментарии заменено на `git log --grep='pre-commit'`.

## Что НЕ изменилось

- Исторические упоминания DEV_HISTORY в комментариях кода (`utils/text_utils.py`, `modules/scheduler/__init__.py`, `database/models.py` и др.) и в SQL-миграциях — оставлены как historical markers, не активные ссылки.
- Immutable mailbox-письма в `mailbox/to-brain/` с упоминаниями DEV_HISTORY не правились (`2026-05-22-mailbox-protocol-acknowledged.md`, `2026-05-23-asymmetry-migration-done.md` и др.).
- Worktree `.claude/worktrees/dev/` (ветка `chore/dev-sandbox`) — не трогался, там старая версия `.claude/commands/*` остаётся (это чужая зона, владелец её обновит когда merge или rebase).
- Братские проекты Gonba / MatricaRMZ продолжают использовать `docs/DEVELOPMENT_LOG.md` — это их решение, не за setka. По pool #004 они тоже кандидаты, но без давления.

## Adaptation notes (для pool #004)

1. **Granularity ссылок в PENDING_FOLLOWUPS**: исторические markers «закрыто YYYY-MM-DD, см. DEV_HISTORY.md» оставлены **намеренно** — они теперь работают как pointers в `git show <commit>~N:docs/DEV_HISTORY.md`. Это дёшево читать (один git command) и сохраняет immediate context для закрытой задачи без необходимости копать через 30 коммитов в `git log`.
2. **Failed approaches секция в SESSION_HANDOFF уже есть** — последняя живая нитка (F601 monitoring) имеет 2 failed approaches с подробным «почему отвергли». Идея #0006 (Failed approaches секция) фактически предшествовала #004 в setka, не отдельным PR.
3. **«Пожить 2-3 нитки» — не выполнено формально**: pool рекомендует «убедиться что уроки реально попадают в Failed approaches до упразднения DEV_HISTORY». У нас прожита **1 нитка** с SESSION_HANDOFF (F601), мало. Но: пользователь дал прямую команду «делай сейчас», и контекст текущего дня — после 5 рефакторных PR — особенно показывает преимущества git-based хронологии (5 описательных commit messages с body несут больше актуального контекста чем 5 параллельных DEV_HISTORY-записей, которые мы бы дублировали).
4. **Mailbox check в /start — баг**: `Glob` относительный путь вне корня проекта не работает (вернул «No files found» для `../brain_matrica/mailboxes/setka/from-brain/*.md`, хотя файл там есть). Workaround — `Bash ls`. Этот баг важен для всех проектов, у которых есть brain-mailbox — стоит документировать в pool #003 или в brain's own «How to use mailbox» guide.

## Follow-up для brain

- В pool #004 обновить статус setka → ✅ 2026-05-24 с ссылкой на ADR-0001.
- В `projects/setka.md` обновить раздел «Применённые идеи из pool»: `#004 ✅ 2026-05-24`.
- В разделе «Особенности / стратегические долги» в `projects/setka.md` — снять строку про «свой стиль docs DEV_HISTORY», теперь setka выровнен с минималистичным паттерном (SESSION_HANDOFF + ADR + git log).
- **Опционально**: рассмотреть включение mailbox-check-via-Bash adaptation note в pool #003 (или в новый brain-side guide) — чтобы другие проекты не наступали на тот же Glob-баг.

## Связано

- ADR-0001 в setka: `docs/adr/0001-archive-dev-history.md`
- PR в setka: <hash появится при merge>
- Pool #004: [cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md](../../../brain_matrica/cross-project-ideas/ideas/004-minimalist-ai-docs-2026.md)
- Письмо-фундамент: `2026-05-23-adopt-session-handoff.md` (про идею #003 SESSION_HANDOFF — предусловие для #004)
