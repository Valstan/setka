# CLAUDE.md

Перед любым действием полностью прочитай [`AGENTS.md`](AGENTS.md) и следуй ему как
каноническим проектным правилам. **Здесь проектных правил нет и не должно быть** —
две копии канона расходятся молча ([ADR-0011](../brain_matrica/adr/0011-vendor-neutral-agent-contract.md)).

Ниже — только то, что специфично для Claude Code как инструмента.

## Где что лежит

- Исполняемые памятки процедур — [`.claude/commands/`](.claude/commands/), вызываются
  как slash-команды (`/start`, `/reliz`, `/close_session`, `/check`, `/celery`, `/logs`,
  `/sql`, `/obriv`, `/distill`, `/deadcode`, `/discover_communities`, `/curate`).
- Командная политика разрешений и SessionStart-хук git-sync — [`.claude/settings.json`](.claude/settings.json).
  Файл общий, коммитится и разъезжается на все машины владельца.
- Локальные разрешения конкретного компьютера — только в игнорируемом
  `.claude/settings.local.json`.

## Нюансы инструмента

- **Правила permissions сопоставляются по префиксу и по имени инструмента:**
  `Bash(...)` ≠ `PowerShell(...)`, поэтому в `settings.json` живут оба набора
  (Windows-машины гоняют команды через PowerShell). Префикс **не различает** read-only
  `ssh sarafan "..."` от destructive `ssh sarafan "...psql DROP..."` — дискриминатор внутри
  кавычек. Отсюда правило канона: destructive-гейт держится поведением, а не конфигом.
- **Человеческий гейт #025** (destructive прод-операции — см. `AGENTS.md` §«Автономия под
  гейтами») исполняется через `AskUserQuestion`: **блокирующий вопрос с ожиданием ответа**,
  а не фраза в тексте с продолжением работы в том же ходе. Так же читаются все
  `AskUserQuestion` внутри памяток `.claude/commands/*.md`.
- **Auto-mode classifier** блокирует SSH-команды на прод как «Production Reads» — их
  нужно подтверждать через `AskUserQuestion` либо разрешать в `settings.json` на сессию.
  Это удобство, а не гейт: ответственность за прод-подтверждения по `AGENTS.md` лежит
  на агенте, а не на фильтре.
- **Авто-архивацию сессий** (Claude Desktop → вкладка **Cowork** → «Classify session
  states») при желании отключить вручную — это UI-настройка, не ключ `settings.json`.
  Sync-гейт и SessionStart-хук защищают независимо от неё.
- В коммитах подписывайся собой: `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`
  (версию — свою фактическую).
