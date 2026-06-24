# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (mid-flight кода нет; ждут: ad-CRM триаж инбокса (brain GO) + решение владельца 1×/2× «Кругозор»)
**Updated:** 2026-06-24
**Branch:** main
**Last release in prod:** прод HEAD `6168cd9` (#275). Все сервисы active, health 200 (probe 2026-06-24). Миграции 046+047 применены. **Прод-правка nginx вне git в силе:** `client_max_body_size 20m` на уровне `http`.

---

## Текущая нитка

Активной mid-flight нитки нет. Сессия 2026-06-24 закрыла две вещи:

1. **`digest` → `bulletin` / «дайджест» → «Сводка» по ВСЕЙ системе — ЗАВЕРШЕНО + задеплоено**
   (5 PR: [#271](https://github.com/Valstan/setka/pull/271) текст постов → [#272](https://github.com/Valstan/setka/pull/272)
   операторский UI/уведомления → [#273](https://github.com/Valstan/setka/pull/273) идентификаторы кода +
   рус. комментарии → [#274](https://github.com/Valstan/setka/pull/274) БД/env/Celery/routes + миграция 046 →
   [#275](https://github.com/Valstan/setka/pull/275) long-tail переменные/комментарии/JS + миграция 047).
   Перед long-tail прогнан audit-workflow (15 агентов) на персистентные/контрактные токены —
   нашёл один скрытый (`regions.config.digest_mode`, мигрирован 047, 2 области). **Остаток `digest`
   в коде = ТОЛЬКО защищённое** (stdlib `hexdigest`/`compare_digest`/`digest_size`/`.digest()`,
   метрики Prometheus `setka_digest_*`+`track_digest_published`, Redis-ключ `setka:digest_last_published`,
   имя файла миграции 046). Русское «дайджест» = 0. Не пытаться «дочистить» — это stdlib + телеметрия.

2. **Эмпирический замер охвата «Кругозора»** ([#270](https://github.com/Valstan/setka/pull/270),
   `scripts/probe_krugozor_reach.py`): итог — **оставить 1×**. Сводку читают (median 180 просм./пост),
   но в типичном регионе это ~47% локального поста, вовлечённость ≈0. Решение 1×/2× — за владельцем.

## Следующий шаг

1. **ad-CRM триаж инбокса** — brain дал **GO** (`recommend`, письмо `from-brain/2026-06-23-adcrm-phase2-go-and-audio-built.md`):
   маленькая нить — порог/скрытие `score=0` (авто-`skipped` для нулевого шума) + сортировка/бейдж по `score`
   в инбоксе `/ad`; разовая bulk-зачистка исторических 418 низкого score (**деструктив → подтвердить
   с владельцем в том же ходе, #025**); после — снять **одну** эмпирическую точку (насколько похудел
   инбокс, не утонула ли ценная заявка). **Не разворачивать в большую программу.** Ответ brain про
   обработку этой директивы пока не отправлен — при старте нити либо реализовать+отчитаться, либо ack.
2. **«Кругозор» 1×/2×** — если владелец захочет роста: включить обеденный слот 13:00 как *измеряемый
   эксперимент* и перезамерить `scripts/probe_krugozor_reach.py` через ~2 недели (сравнить суммарный
   дневной охват, а не на пост).

## Контекст

- **План:** активного плана-файла нет.
- **Связанные коммиты сессии:** `9a0842c` (#270 krugozor probe), `692560a` (#271), `6f37c49` (#272),
  `0b01b24` (#273), `7009ba4` (#274 + миграция 046), `6168cd9` (#275 + миграция 047).
- **Прод:** все сервисы active, health 200, HEAD `6168cd9` = origin/main. Celery: `create_daily_bulletin` +
  `check_bulletin_heartbeat` зарегистрированы, beat-schedule очищен/перезапущен (ключи `bulletin-*`).
- **Открытых PR:** этот handoff-PR (doc-only + письмо-находка, авто-merge). #270–#275 смержены.

## Failed approaches (этой нитки)

_Не было — rename прошёл чисто (audit-workflow + placeholder-protect + тесты-гейт)._

## Открытые вопросы для пользователя

- ad-CRM триаж инбокса — начинать следующей нитью? (brain GO ждёт)
- «Кругозор» — включаем 2× как эксперимент или оставляем 1×?

## Не забыть (low-priority)

- 📬 Письмо brain `2026-06-23-adcrm-phase2-go-and-audio-built.md` (`recommend`) — обработать/ответить
  при старте ad-CRM нити (молчать на `recommend` нельзя по ADR).
- 🟢 Письмо-находка ушло в `mailbox/to-brain/2026-06-24-safe-large-rename-playbook.md` (playbook
  массового rename) — в этом же закрывающем PR.
- ⏱ Stale-пункт `PENDING`: «Пакет браузер-верификаций владельцем» (snooze 3+, с 2026-06-03) — так и
  ждёт ре-триажа (переформулировать в standing-проверки / снять `stale`).
- 🟢 Следующий dead-code прогон ~2026-07-14.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
