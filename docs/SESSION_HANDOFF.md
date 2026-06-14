# Session Handoff

> Sticky-note для непрерывности между сессиями разработки SETKA. Перезаписывается через [`/close_session`](../.claude/commands/close_session.md) — историю смотри через `git log --follow -- docs/SESSION_HANDOFF.md`.

**Status:** ACTIVE (несколько ниток в работе/эксплуатации; mid-flight стройки нет — всё этой сессии задеплоено)
**Updated:** 2026-06-14
**Branch:** main
**Last release in prod:** прод на `c043a5e` (= main; web+worker+beat перезапущены, health 200). Все 14 PR сессии задеплоены инкрементально.

---

## Текущая нитка

Большая сессия 2026-06-14: **14 PR** ([#227](https://github.com/Valstan/setka/pull/227)–[#239](https://github.com/Valstan/setka/pull/239)) + 3 прод-правки вне git (ниже). Mid-flight стройки нет — всё задеплоено. Активны несколько standing-ниток и открытых директив, ждущих **ввода владельца** или следующего шага.

**Что сделано и живёт в проде:**
- **LLM-курация (Ф1 PoC закрыт):** цифры сняты (flag-rate 18.81%, 19/101), ack brain + редполитика в рубрику (научпоп/познавательное = keep; reroute рекламы). Открыто: решение Фазы 2 за владельцем/brain + спот-чек владельца на 19 флагах (precision). `scripts/curate_pending.py --flagged` выгружает их.
- **«Кругозор» — научпоп-дайджест веером** ([#230](https://github.com/Valstan/setka/pull/230)/[#232](https://github.com/Valstan/setka/pull/232)/[#233](https://github.com/Valstan/setka/pull/233)): `modules/krugozor_broadcast.py`, дайджест 2-4 поста из РАЗНЫХ источников (ротация) на 16 пабликов, лид-фото грид, анти-промо фильтр. **12 источников** (category=krugozor: SciTopus/НауЧпок/Batrachospermum/Время-Вперёд + добавил ПостНаука/N+1/Образовач/Наука-и-жизнь/Антропогенез/TechInsider/Arzamas/Кот-Шрёдингера). Beat 20:00 MSK. **Включён** (env `KRUGOZOR_BROADCAST_DISABLED=0`).
- **Радар↔Телега починен:** корень — 0 подписок (поллер видел `sources:0`). Восстановил подписку valstan→gonba_life (прод-правка вне git). TG-чтение через relay исправно.
- **Радар intake-бот «приём каналов»** ([#235](https://github.com/Valstan/setka/pull/235)–[#238](https://github.com/Valstan/setka/pull/238)): форвард поста канала боту → канал в радар. `modules/radar/bot_intake.py` (getUpdates-polling, молчит чужим, гейт на allowlist), сервис `modules/radar/subscriptions.py`. **Включён на AFONYA** (@malm_info_bot): env `RADAR_BOT_NAME=AFONYA` + `RADAR_BOT_ALLOWED_USERS=352096813` (прод-правка вне git).
- **ad-CRM:** сигнал brain «push ушёл в эксплуатацию» (real-use clock) + **кнопка «Опубликовать»** ([#239](https://github.com/Valstan/setka/pull/239)) — моментальная бесплатная публикация бытовой заявки из предложки (POST `/ad-cabinet/requests/{id}/publish`).

## Следующий шаг

Mid-flight задачи нет. Кандидаты (по приоритету):
1. **Ответить brain на probe сетевой рассылки** — директива `2026-06-14-network-broadcast-internal-scheduler.md` ждёт probe прав постинга + флуд-лимитов. **Данные уже есть из krugozor:** 16 `wall.post` @5с интервал = 16/16 без капчи; 16 `wall.edit` @3с (бэкфилл) словил капчу. Охват = 16 активных пабликов. Написать ответ в `mailbox/to-brain/`, затем рассылка = обобщение copy_setka/krugozor (переиспользовать ad-CRM С2-планировщик, не в VK-отложку).
2. **Браузер/Telegram-проверка владельцем** (наружу-действия, за владельцем): кнопка «Опубликовать» на бытовой заявке (Ctrl+Shift+R для нового JS); форвард канала боту @malm_info_bot → проверить, что канал упал в радар.
3. **Авто-приветствие** (#222) — текст согласован, ждёт от владельца **vk-id сообществ** для `AD_AUTO_GREETING_COMMUNITIES`.
4. **Brain-директива генератора обложек** (`2026-06-14-community-cover-template-generator.md`) — probe cover-API (`photos.getOwnerCoverPhotoUploadServer`/права) на скольких пабликах админ; пилот Верхошижемье.

## Контекст

- **План:** активного плана-файла нет; нитки вели по brain-письмам + запросам владельца.
- **Прод-правки вне git (записать при следующем pull):** (1) `radar_subscriptions` — восстановлена подписка valstan→gonba_life; (2) `/etc/setka/setka.env` — добавлены `KRUGOZOR_BROADCAST_DISABLED=0`, `RADAR_BOT_NAME=AFONYA`, `RADAR_BOT_ALLOWED_USERS=352096813`. Миграций в сессии не было.
- **Прод:** все сервисы active, HEAD `c043a5e`, health 200. Krugozor-дайджест и radar-intake живут на проде.
- **Открытые brain-письма (2 директивы, `recommend`):** `2026-06-14-network-broadcast-internal-scheduler.md`, `2026-06-14-community-cover-template-generator.md` — обе probe-first, не начаты.
- **Отчёты brain'у этой сессии:** `mailbox/to-brain/`: `2026-06-14-llm-curation-poc-stats.md`, `2026-06-14-ad-crm-in-operation.md`, `2026-06-14-vk-copyright-param-dropped-gotcha.md` (находка #009).
- **Открытых PR:** нет (этот handoff-PR — doc-only, авто-merge).

## Failed approaches (этой нитки)

- **VK `copyright`-плашка «Источник» для vk.com-ссылок** — не работает, VK молча отбрасывает (probe `wall.getById` → `copyright:null`). Атрибуцию даём текстом (футер). Подробно — `mailbox/to-brain/2026-06-14-vk-copyright-param-dropped-gotcha.md`. **Не пытаться через `copyright`.**
- **Бэкфилл 16 постов через `wall.edit` подряд @3с** — словил VK-Captcha (как и copy_setka @5с на бурсте). Массовые правки/посты держать на интервале ≥5с; для общего токена бэкфилл не доделывали (косметика, забили по решению владельца).
- **Intake-бот на публичном боте с ответом чужим** — @malm_info_bot/@valstan_bot имеют входящий трафик (111/116 в очереди, всё авто-шум/channel_post, не вопросы). Поэтому приёмник **молчит чужим** + гейт на allowlist (иначе спам сотне людей).

## Открытые вопросы для пользователя

- Авто-приветствие: какие сообщества (vk-id) включить в `AD_AUTO_GREETING_COMMUNITIES`?
- LLM-курация Фаза 2: enforcing vs Haiku-API — после спот-чека владельца на 19 флагах.
- Безопасность: токен AFONYA мелькнул в транскрипте сессии — перевыпустить у @BotFather, если транскрипт не приватный (в логах прода погашено).

## Не забыть (low-priority)

- 🟢 9 первых krugozor-постов со старым футером (без ссылки) — оставлены по решению владельца (капча, косметика).
- 🟢 @malm_info_bot получает поток эхо-дайджестов в getUpdates (мелкий бардак линковки канал↔группа) — приёмнику безвредно, можно отдельно разобраться.
- 🟢 Krugozor: после ~недели замерить охват (просмотры/лайки) → решить 1×/день vs +обед 13:00.

---

> Если читаешь это в начале новой сессии — обнови через `/close_session` в конце. **Не аккумулируй history тут** — она в `git log --follow -- docs/SESSION_HANDOFF.md`.
