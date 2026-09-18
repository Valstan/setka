# ~~🔴 Радар остался без VK-интейка: удалён `COMM_137760500`~~ ЗАКРЫТО 2026-07-25

> Запись `P147` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-07-25 · закрыто в тот же день`

При чистке таблицы токенов удалён `COMM_137760500`. Я подал его владельцу как
«осиротевший токен неактивного региона `test`», потому что проверил только связь
с таблицей `regions`. Проверка была неполной: **эта группа — не регион**, её держит
env `RADAR_VK_COMMUNITY_ID=137760500`, и токен обслуживал две живые функции Радара:

1. **VK-интейк** (`tasks.radar_tasks.poll_radar_vk_intake`, каждую минуту) — приём
   кодов привязки VK-лички через Bots Long Poll. До удаления отрабатывал успешно
   (`ok: True`), после — `skipped: no community token for 137760500`.
2. **Доставка `vk_dm`** (`modules/radar/delivery.py:_make_default_vk_dm_sender`) —
   есть **1 активный** output типа `vk_dm`, его дайджесты сейчас не уходят.

**Чем закончилось.** Значение из БД было невосстановимо (дампов на боксе нет), но
**сам ключ в VK никуда не делся** — удалена была только строка с его значением.
В «Управление → Работа с API» он лежал на месте (`vk1.…Q_Ow`, создан 19 мая в 17:45 —
ровно `last_validated` удалённой строки). Владелец раскрыл его кнопкой «Показать» и
вернул через `/tokens`. Проверено: строка в БД `valid`, права те же
(`photos/docs/messages/wall/manage/stories/market`, маска 134623237),
`poll_radar_vk_intake` в 21:28 ещё отдавал `skipped`, а в 21:29 — `ok: True`.

**Побочно выяснено:** раздел «Работа с API» виден **только владельцу** — под МАМОЙ
его нет, под VALSTAN есть. Это подтверждает owner-only ограничение VK, которое
раньше знали только по API-пробе.

**Урок:** перед удалением community-токена сверять не только с `regions`, но и с
env-переменными, которые указывают на community_id (`RADAR_VK_COMMUNITY_ID`,
`VK_TEST_GROUP_ID`) — иначе «осиротевший» токен окажется рабочим.

- ~~**VK-токен VALSTAN не имеет scope `wall`/`likes`**~~ Закрыто 2026-05-26 (этот PR): попытка получить токен с `wall`+`groups` через четыре разных способа провалилась — VK 2026 (а) у публичных mobile-app_id (Kate Mobile, VK Messenger, VK Mobile) либо режет scope (отдаёт `[photos, email, ads, offline]`), либо привязывает токен к IP-адресу выпуска (error 5 `access_token was given to another ip address` при обращении с прод-VPS); (б) для своего Standalone-приложения VK закрыл новую форму создания (на dev.vk.com доступны только Мини-приложение / Игра / Плагин для сообществ), legacy URL `vk.com/editapp?act=create` тоже больше не показывает Standalone; (в) `likes.add` через community-token VK явно отказывается обслуживать с error 27 `Group authorization failed: method is unavailable with group auth`. **Решение**: кнопка ♥ в `/notifications` теперь — обычная ссылка-deeplink `https://vk.com/wall{owner}_{post}?reply={cid}&thread={cid}`, открывает пост в VK с фокусом на комменте, лайк ставится руками в VK. Backend endpoint `/api/notifications/comments/like` оставлен в коде на случай если когда-нибудь scope `wall` снова станет доступен для физлиц.
- ~~**Discovery trigger длится >180s — nginx обрывает клиента**~~ Закрыто 2026-05-25 ([PR #49](https://github.com/Valstan/setka/pull/49), `0edf84b`): trigger переведён на Celery + UI polling через `/api/discovery/task/{id}/status`. UI больше не виснет. Nginx полу-фикс 600s в `/etc/nginx/conf.d/setka.conf` остался — не мешает, можно при желании откатить на 180s.
- ~~**Groq API key возвращает 403 Forbidden**~~ Переведено в 🟡 техдолг 2026-05-26: discovery больше не зависит от Groq (PR #41 AI-batch через clipboard, PR #51 info-repost). Затрагивает только UX-фичу — AI-черновик ответа на VK-комменты в `modules/notifications/ai_drafter.py` (модератор пишет вручную). См. 🟡 ниже.

---
