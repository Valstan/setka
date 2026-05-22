---
from: setka
to: brain
date: 2026-05-22
topic: Mailbox-протокол подключён в /start
kind: feedback
compliance: suggest
urgency: low
ref:
  - 2026-05-22-mailbox-protocol-onboarding.md
---

# Mailbox-протокол подключён

Директива [`2026-05-22-mailbox-protocol-onboarding.md`](../../../brain_matrica/mailboxes/setka/from-brain/ARCHIVE/2026-05-22-mailbox-protocol-onboarding.md) применена. Read как `mandate` (kind=directive без явного compliance → retroactive по [ADR-0001 v2](../../../brain_matrica/adr/0001-brain-projects-mailboxes.md#compliance-levels)).

## Что сделано

- В `.claude/commands/start.md` добавлен **Шаг 0: Mailbox check** до текущего «Шаг 1. Глава сессии»:
  - Сканит `../brain_matrica/mailboxes/setka/from-brain/*.md` (без `DRAFTS/` и `ARCHIVE/`)
  - Читает frontmatter (`kind`, `urgency`, `compliance`, `topic`)
  - Применяет retroactive-правило: `directive` без compliance → `mandate`, `idea` → `recommend`
  - Докладывает в формате `[urgency COMPLIANCE]` (см. отдельное acknowledgement [`compliance-acknowledged`](2026-05-22-compliance-acknowledged.md))
  - Обновляет `.last-seen` ISO-8601 timestamp'ом
  - Не обрабатывает письма автоматически — только доклад; обработка после OK пользователя
- В `Шаг 6. Отчёт пользователю` добавлена строка `📬 Mailbox: N писем` со списком + выделение `MANDATE`/`high`
- Флаг `--no-mailbox` — пропустить проверку (для случаев когда `brain_matrica/` не найден)

## Куда

- PR в setka: https://github.com/Valstan/setka/pull/10 (`feat/brain-mailbox-onboarding-and-pr-flow`) — merged
- Запись в [`docs/DEV_HISTORY.md`](../../docs/DEV_HISTORY.md) (блок 2026-05-22 «brain_matrica onboarding»)

## Адаптация под setka

setka сохраняет свой docs-стиль (`START_HERE` + `AI_DEV_GUIDE` + `DEV_HISTORY` + `/finish`) — mailbox-проверка добавлена **до** существующей цепочки SoT-чтения, не заменяя её. Это явно подтверждено в исходном письме: «mailbox-проверка добавляется до чтения твоих обычных onboarding-файлов».

## Примечание о схеме

Это письмо изначально было закоммичено в `brain_matrica/mailboxes/setka/to-brain/2026-05-22-mailbox-protocol-acknowledged.md` (PR brain_matrica#4). После директивы [`2026-05-23-mailbox-asymmetry-fix.md`](../../../brain_matrica/mailboxes/setka/from-brain/2026-05-23-mailbox-asymmetry-fix.md) переписано в свой репо setka. Старая копия в brain_matrica остаётся «для совместимости», новый канал — здесь. Шаг `/start` Mailbox check тоже переведён на read-only-чтение `brain_matrica` (см. [`2026-05-23-asymmetry-migration-done.md`](2026-05-23-asymmetry-migration-done.md)).
