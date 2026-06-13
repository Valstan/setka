# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (standing-программы; mid-flight нитки нет — всё этой сессии задеплоено/закрыто)
**Updated:** 2026-06-14
**Branch:** main
**Last release in prod:** прод на `1349055` = main минус doc-only #225 (Ф1.2 квоты задеплоены, 4/4 active, health 200).

---

## Текущая нитка

**Эта сессия — радар Ф1 закрыт целиком** (MANDATE brain 2026-06-13, приоритизация Ф1):
1 ретенция ✅ (была в Ф0.4) · **2 enforcement квот ✅ задеплоено** ([#224](https://github.com/Valstan/setka/pull/224)) ·
3 PNG-иконки ✅ (уже были в Ф0.4, манифест install-ready) · **4 TG-медиа ✅ probe закрыл как
нежизнеспособное** ([#225](https://github.com/Valstan/setka/pull/225)). Открытая развилка — только
residential-egress прокси, `parked` до явного запроса владельца «файлы медиа в архиве».

Заодно проведена **браузер-верификация** под логином владельца (Claude-for-Chrome): ad-CRM С1–С5,
tiered-поиск #035, Σ/без-дублей, тёмная тема, `/monitoring`, `/tokens`, радар-Ф0, DM-роутер — всё работает.

**Standing-программы (ритм, не закрыты):** ad-CRM (еженедельный круг улучшений, MANDATE) и хвост
LLM-курации (ждёт цифр PoC).

## Следующий шаг

Mid-flight задачи нет — нитка радар-Ф1 закрыта. Ближайшие по календарю:
1. **~2026-06-14 (наступило): цифры PoC LLM-курации** → ack brain (ждёт решения Фазы 2). Команда:
   `ssh setka "cd /home/valstan/SETKA && sudo bash -c 'set -a; source /etc/setka/setka.env; set +a; ./venv/bin/python scripts/curate_pending.py --stats'"`.
2. **~2026-06-20: ad-CRM раунд-2** — посмотреть статистику (`/funnel` total_views/debtors, время первого
   отклика, сколько авто-приветствий ушло) → предложить владельцу следующее улучшение, реализовать.
3. **Авто-приветствие (#222)** — спит, ждёт включения владельцем (env `AD_AUTO_GREETING_COMMUNITIES` + текст).

## Контекст

- **План:** активного плана-файла нет; радар-Ф1 вёлся по brain-письму `2026-06-13-content-radar-f1-prioritization.md`.
- **Связанные коммиты сессии:** `1349055` [#224](https://github.com/Valstan/setka/pull/224) Ф1.2 квоты (код, задеплоено),
  `2e0de38` [#225](https://github.com/Valstan/setka/pull/225) Ф1.4 probe-результат + Ф1-доки (doc-only).
- **Прод:** HEAD `1349055` (после deploy Ф1.2, restart web), 4/4 active, health 200, диск 54.8% (10.6 ГБ, своб. 4.56 ГБ).
- **Отчёты brain'у этой сессии:** `mailbox/to-brain/2026-06-13-radar-f1-quota-enforcement.md`,
  `mailbox/to-brain/2026-06-13-radar-tg-media-probe-result.md` (probe + уточнение к G56).
- **Открытых PR:** нет (этот handoff-PR — doc-only, авто-merge).

## Failed approaches (этой нитки)

- **Фоновый воркер скачивания TG-медиа с ретраями** — DOA, подтверждено probe 2026-06-13. Бокс setka
  **жёстко блокирует** `*.telesco.pe` на connection-level (`ConnectError: All connection attempts failed`,
  0/10), это **не G56-тарпит** (трикл), а полный блок egress'а. Сквозь refused-коннект ретраи не помогают.
  **Не строить** без residential/неблокируемого egress'а. Медиа в архиве — text+link (владелец: «не критично»).

## Открытые вопросы для пользователя

- Включаем авто-приветствие рекламодателю сейчас? Нужны: текст + список сообществ (VK-id).
- Понадобятся ли когда-нибудь **файлы** TG-медиа в архиве радара? Если да — развилка residential-egress
  (стоимость + поддержка); если нет — оставляем text+link навсегда (текущее).

## Не забыть (low-priority)

- 🟡 Управление авто-приветствием — пока через env (per-community allowlist); кандидат на UI-тумблер.
- ⏸ Residential-egress прокси для TG-медиа — `parked` до явного запроса владельца.
- ⏸ AI-дедуп (embeddings) — `parked` до VPS ≥4 ГБ.
- 🟢 Браузер-остаток ad-CRM: С3 «Обновить просмотры»/«Отчёт клиенту» (нужны публикации), push-колокольчик радара.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
