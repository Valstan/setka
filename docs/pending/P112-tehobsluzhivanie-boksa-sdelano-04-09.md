# 🧰 Техобслуживание бокса (mandate brain 2026-08-30, срок 06.09) — ✅ СДЕЛАНО 04.09

> Запись `P112` реестра [PENDING_FOLLOWUPS](../PENDING_FOLLOWUPS.md). Индекс грепается, запись читается целиком, реестр целиком не читается никогда ([D-097](../../AGENTS.md)).

`⏱ 2026-08-30 · snooze 0 · ✅ ЗАКРЫТО 2026-09-04 — отчёт мозгу ушёл до срока`

Олламы на боксе не было (ни юнита, ни бинарника, ни `~/.ollama`); 227 пакетов обновлены,
0 upgradable, ребут не требуется; journald `SystemMaxUse=200M`; кэши pip сняты; диск 6.6G → 6.4G.
**Корень сбоя `apt upgrade` — tzdata:** `/etc/localtime` → `/usr/share/zoneinfo/Host` (файл
хостера, MSK), зоны `Host` в tzdata нет → postinst код 10 → 25 пакетов без configure (postgresql-17,
python3.12). Вылечено без смены оффсета: `Europe/Moscow` в `/etc/localtime`, `/etc/timezone`,
debconf (там стояло `Etc/UTC` — интерактивная реконфигурация переключила бы бокс на UTC).
**Побочная находка:** в `postgresql.conf` `timezone = Host` / `log_timezone = Host` — корень
секции «`DEFAULT now()` отдаёт MSK»; файл `Host` оставлен, менять — только релизом с миграцией
(см. ту секцию). Отчёт: `mailbox/to-brain/2026-09-04-box-maintenance-done-…md`.
