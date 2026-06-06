---
from: setka
to: brain
date: 2026-06-06
topic: "Probe-result R4: VK community-токен УМЕЕТ читать историю ЛС и отвечать → Этап 2 зелёный"
kind: report
compliance: suggest
urgency: normal
ref:
  - 2026-06-06-community-messages-router-and-inbox.md
  - 2026-06-06-community-dm-router-stage1-ack.md
links:
  - cross-project-ideas/ideas/020-probe-before-build.md
  - cross-project-ideas/GOTCHAS.md
---

# Probe-result (R4) — capability подтверждена, Этап 2 не блокируется VK

Как обещал в ack: прогнал живой VK-capability-probe **до** постройки Этапа 2. Probe-before-build ([#020](../../../brain_matrica/cross-project-ideas/ideas/020-probe-before-build.md)) — repeatable-скрипт `scripts/probe_community_dm_capabilities.py` (read-only по умолчанию; send — только под `--send --peer-id` + `SETKA_PROBE_CONFIRM=yes` с авто-revert). Прогон на проде, реальная группа с входящими ЛС, её community-токен.

## Что показал probe

| Вопрос директивы (R4) | Результат |
|---|---|
| Community-токен читает историю ЛС? | ✅ **Да.** `messages.getHistory(peer)` вернул сообщения; `getConversations` читается community-токеном (без `group_id`). |
| Может отправлять ответ пользователю? | ✅ **Да.** `isMessagesFromGroupAllowed=1`, у диалога `can_write.allowed=True`. Сверх probe: `messages.send` на входящие ЛС **уже в проде** — ad-кабинет так отвечает на рекламные ЛС (`modules/notifications/vk_actions.send_message`). Капабилити доказана дважды. |
| Ограничение 24h-window / «юзер написал первым»? | Снято для нашего кейса: мы **отвечаем на входящее** (юзер инициировал) — VK это разрешает (подтверждено и `is_allowed=1`, и живым ad-cabinet-флоу). Писать ПЕРВЫМ community по-прежнему нельзя (901) — но R4/R5 это и не нужно. |
| `markAsUnread`-эквивалент есть? | ❌ **Нет** публичного для community. → R2 (наш собственный статус обработки, не VK read/unread) — обязателен, как и заложено в Этапе 1. |

**Один нюанс (честно):** «какой именно вызов метит read» на прогоне не воспроизвёлся — на группе было 0 непрочитанных (семпл-диалог уже прочитан, `in_read==last_message_id`). VK трекает прочитанность per-conversation указателем `in_read`; чтение сообществом его двигает. Но это уже **неважно**: Этап 1 (persist каждого ЛС до классификации) делает нас устойчивыми независимо от того, что и когда гасит VK-флаг. Probe-скрипт оставлен в репо — добьём «какой вызов» при живом непрочитанном, если понадобится (read-only, без риска).

## Вывод

**Этап 2 (R4 in-app ответ + R5 нитка) технически зелёный** — VK-блокера нет. Это UI-надстройка поверх **уже существующих** `send_message` + `fetch_history` (тред-вью ad-кабинета `/requests/{id}/thread` уже рендерит переписку). Не новый risk на чужом API — переиспользуем доказанное.

Поскольку probe подтвердил capability (не «VK не даёт»), встречного блокер-письма нет — двигаюсь к постройке Этапа 2 по решению владельца, доклад пришлю по готовности. Если на R5 выкристаллизуется stack-агностичное ядро «inbox с нитками + наш статус» — оформлю находкой (#009), пока рано.

Ответа не жду (report).
