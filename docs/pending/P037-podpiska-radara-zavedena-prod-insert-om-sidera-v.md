# 🟡 Подписка радара заведена прод-INSERT-ом — сидера в git нет

> Запись `P037` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-08-28 · snooze 0 · fresh · вынесено из секции «📡 Радар — чтение Телеги + intake-бот» при ре-триаже`

Подписка `valstan→gonba_life` в `radar_subscriptions` заведена прямым прод-INSERT-ом вне git;
grep `radar_subscriptions` по `scripts/` и `database/migrations/` не даёт ни одного файла-сидера —
при пересборке БД подписка теряется молча, и поллер снова увидит `sources:0`. Env intake-бота
(`RADAR_BOT_NAME`, `RADAR_BOT_ALLOWED_USERS`) восстановимы — они перечислены в
`modules/secrets_bootstrap.py:119-120`; воспроизводима из репозитория только эта половина.
