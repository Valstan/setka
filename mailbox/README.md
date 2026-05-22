# mailbox — связь setka ↔ brain_matrica

Папка для асимметричного mailbox-протокола ([brain_matrica/adr/0001](../../brain_matrica/adr/0001-brain-projects-mailboxes.md), v3 от 2026-05-23).

## Правила

- `to-brain/` — **я пишу здесь** и коммичу в setka репо. brain читает через `cd ../setka && git pull --ff-only`.
- `brain_matrica/mailboxes/setka/from-brain/` — **brain пишет туда** и коммитит в brain_matrica. Я читаю через `cd ../brain_matrica && git pull --ff-only` (read-only, никаких записей).

Каждая сторона владеет своим репо. Кросс-репо коммиты запрещены ([asymmetry-fix](../../brain_matrica/mailboxes/setka/from-brain/2026-05-23-mailbox-asymmetry-fix.md)).

## Архивация

Сейчас не делается (MVP). Папка `to-brain/` накапливает письма; brain читает у себя, помечает у себя. Если разрастётся — добавим механизм отдельно.
