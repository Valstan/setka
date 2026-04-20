# Удалённый доступ к продакшену SETKA

Документ задаёт **единое правило** для AI-разработчиков и людей: как безопасно работать с сервером проекта SETKA и не путать его с другими хостами.

---

## Главное правило

1. **Единственный поддерживаемый способ для задач SETKA — SSH** к хосту, где развёрнут проект (`/home/valstan/SETKA`). Полный shell: `git`, `systemctl`, логи, `curl`, отладка, произвольные команды.
2. **MCP-серверы в Cursor/IDE для SETKA не используются** — они путают разные VPS и проекты. Агентам и людям: деплой, диагностика и прод всегда через **стандартный SSH** (см. конфиг ниже). Лишние MCP в настройках IDE лучше **отключить** для этого репозитория.
3. Если сомнение — проверяй **`hostname`**, **`pwd`**, наличие **`/home/valstan/SETKA/main.py`**.

---

## Целевой хост SETKA

| Что | Значение |
|-----|----------|
| Проект на сервере | `/home/valstan/SETKA` |
| Пользователь | `valstan` (как правило) |
| API (локально на сервере) | `http://127.0.0.1:8000` (наружу — Nginx) |
| Сервисы systemd | `setka`, `setka-celery-worker`, `setka-celery-beat` |
| Секреты | `/etc/setka/setka.env` (не коммитить) |

Точный **HostName**, **Port** и **IdentityFile** задаются в **локальном** `~/.ssh/config` (у каждого разработчика свой). Пример структуры (без реальных секретов):

```sshconfig
Host setka
    HostName <ваш_хост_SETKA>
    Port <порт>
    User valstan
    IdentityFile ~/.ssh/id_rsa
    ServerAliveInterval 60
    ServerAliveCountMax 3
```

Подключение:

```bash
ssh setka
cd /home/valstan/SETKA
```

Проверка, что это SETKA:

```bash
test -f /home/valstan/SETKA/main.py && echo OK_SETKA
```

---

## Типичные операции по SSH

```bash
ssh setka "cd /home/valstan/SETKA && git status && git pull origin main"
ssh setka "systemctl status setka setka-celery-worker setka-celery-beat --no-pager"
ssh setka "curl -sS http://127.0.0.1:8000/api/health/"
```

Логи: `/home/valstan/SETKA/logs/` (см. также [`START_HERE.md`](START_HERE.md)).

---

## Чего не делать

- Не использовать **MCP remote exec** и схожие обходы для деплоя/отладки SETKA — только SSH на нужный хост.
- Не выполнять команды на «похожем» VPS из другого проекта, думая что это SETKA.

---

## Связанные документы

- [`START_HERE.md`](START_HERE.md) — быстрый старт, сервисы, тесты.
- [`DEPLOY.md`](DEPLOY.md) — выкладка, перезапуск сервисов.

*Последнее обновление: 2026-04-21*
