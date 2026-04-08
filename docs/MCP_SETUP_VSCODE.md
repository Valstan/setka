# Инструкция по настройке MCP сервера для SETKA

## Что настроено на VPS

✅ **Управляющий скрипт**: `/home/valstan/scripts/vps-manage.sh`
- Поддерживаемые команды: `status`, `logs`, `start`, `stop`, `restart`, `build`, `deploy`, `deploy-quick`, `migrate`, `git-status`, `git-log`, `disk`, `health`
- ✅ Сделан исполняемым (chmod +x)
- ✅ Созданы директории для логов и бэкапов

✅ **SSH авторизация**: Уже настроена с ключами (5 ключей в authorized_keys)

✅ **Node.js**: Установлен v22.20.0

✅ **Сервисы**: setka (FastAPI), setka-celery-worker, setka-celery-beat

---

## Настройка на Windows + VS Code (Qwen Code / Cline / Roo Code)

### Шаг 1: Убедитесь, что Node.js установлен

Откройте PowerShell и проверьте:
```powershell
node --version
```

Если не установлен — скачайте с https://nodejs.org/ (LTS версию).

### Шаг 2: Сгенерируйте SSH-ключ (если ещё нет)

В PowerShell:
```powershell
ssh-keygen -t ed25519 -C "valstan@vscode-windows"
```

Нажмите Enter для пути по умолчанию (`C:\Users\ВашеИмя\.ssh\id_ed25519`). Пароль можно не ставить.

Затем скопируйте публичный ключ на VPS:
```powershell
type $env:USERPROFILE\.ssh\id_ed25519.pub | ssh valstan@81.177.6.46 "cat >> ~/.ssh/authorized_keys"
```

Проверьте, что вход по ключу работает без пароля:
```powershell
ssh valstan@81.177.6.46 "echo OK"
```

### Шаг 3: Настройте MCP-сервер в VS Code

> **Важно**: Настройка зависит от того, какое AI-расширение вы используете в VS Code.

---

#### Вариант A: Qwen Code (Qwen CLI)

1. Откройте VS Code
2. Откройте палитру команд: `Ctrl+Shift+P`
3. Введите: `Qwen Code: Open Settings` (или найдите настройки Qwen Code в расширении)
4. Перейдите в раздел **MCP Servers**
5. Добавьте сервер с конфигурацией:

```json
{
  "mcpServers": {
    "vps-setka": {
      "command": "npx",
      "args": [
        "-y",
        "ssh-mcp",
        "--host", "81.177.6.46",
        "--port", "22",
        "--user", "valstan",
        "--key", "C:\\Users\\<ВашеИмя>\\.ssh\\id_ed25519",
        "--timeout", "120000"
      ]
    }
  }
}
```

**⚠️ Важно**: Замените `<ВашеИмя>` на ваше имя пользователя Windows (папка в `C:\Users\`).

---

#### Вариант B: Cline / Roo Code (расширение для VS Code)

1. Откройте VS Code
2. Откройте настройки расширения:
   - Нажмите `Ctrl+,` (настройки VS Code)
   - Найдите `Cline` или `Roo Code`
   - Или кликните на иконку расширения в боковой панели → Settings ⚙️ → **MCP Settings**
3. Откроется файл `cline_mcp_settings.json` (или аналогичный)
4. Вставьте следующее:

```json
{
  "mcpServers": {
    "vps-setka": {
      "command": "npx",
      "args": [
        "-y",
        "ssh-mcp",
        "--host", "81.177.6.46",
        "--port", "22",
        "--user", "valstan",
        "--key", "C:\\Users\\<ВашеИмя>\\.ssh\\id_ed25519",
        "--timeout", "120000"
      ],
      "disabled": false,
      "alwaysAllow": []
    }
  }
}
```

**⚠️ Важно**: Замените `<ВашеИмя>` на ваше имя пользователя Windows.

---

#### Вариант C: Continue (расширение для VS Code)

1. Откройте VS Code
2. Откройте палитру команд: `Ctrl+Shift+P`
3. Введите: `Continue: Configure`
4. Откроется файл `config.json` расширения Continue
5. Добавьте в секцию `mcpServers`:

```json
{
  "mcpServers": {
    "vps-setka": {
      "command": "npx",
      "args": [
        "-y",
        "ssh-mcp",
        "--host", "81.177.6.46",
        "--port", "22",
        "--user", "valstan",
        "--key", "C:\\Users\\<ВашеИмя>\\.ssh\\id_ed25519",
        "--timeout", "120000"
      ]
    }
  }
}
```

---

### Шаг 4: Перезапустите VS Code

После сохранения конфигурации **полностью перезапустите VS Code**.

### Шаг 5: Проверьте подключение

Откройте чат AI-ассистента в VS Code и напишите:

```
Покажи статус сервера SETKA
```

AI-агент должен выполнить команду через MCP и показать результат.

Если видите ошибки — проверьте:
1. Путь к SSH-ключу (двойные обратные слеши `\\`)
2. Что SSH-ключ работает: `ssh valstan@81.177.6.46 "echo OK"`
3. Что Node.js доступен: `npx --version`

---

## Что теперь может делать ИИ-агент в VS Code

После подключения, когда вы пишете в чат AI-ассистента, агент получит доступ к инструменту `exec` на вашем VPS.

### Примеры запросов:

| Запрос в чат VS Code | Что агент выполнит на VPS |
|---------------------|---------------------------|
| "Покажи статус проекта SETKA" | `bash /home/valstan/scripts/vps-manage.sh status` |
| "Задеплой последние изменения" | `bash /home/valstan/scripts/vps-manage.sh deploy` |
| "Перезапусти сервер" | `bash /home/valstan/scripts/vps-manage.sh restart` |
| "Покажи логи за последние 200 строк" | `bash /home/valstan/scripts/vps-manage.sh logs 200` |
| "Проверь здоровье сервера" | `bash /home/valstan/scripts/vps-manage.sh health` |
| "Запусти миграции БД" | `bash /home/valstan/scripts/vps-manage.sh migrate` |
| "Покажи git status проекта" | `bash /home/valstan/scripts/vps-manage.sh git-status` |
| "Останови сервер" | `bash /home/valstan/scripts/vps-manage.sh stop` |
| "Запусти сервер" | `bash /home/valstan/scripts/vps-manage.sh start` |
| "Собери проект" | `bash /home/valstan/scripts/vps-manage.sh build` |
| "Покажи место на диске" | `bash /home/valstan/scripts/vps-manage.sh disk` |

---

## Типичный рабочий процесс

1. **Кодите локально в VS Code на Windows**
2. **Пушите в GitHub**: `git add -A && git commit -m "..." && git push`
3. **Говорите агенту в VS Code**: "Задеплой на VPS" — агент через ssh-mcp выполнит `vps-manage.sh deploy` (pull + install + build + restart)
4. **Говорите**: "Покажи логи" — агент покажет вывод journalctl прямо в чате
5. **Если что-то не так**: "Покажи статус" / "Перезапусти"

Вся обратная связь приходит прямо в чат AI-ассистента в VS Code.

---

## Доступные команды vps-manage.sh

```
status          - Показать статус проекта
logs [lines]    - Показать логи (по умолчанию: 100 строк)
start           - Запустить сервис
stop            - Остановить сервис
restart         - Перезапустить сервис
build           - Собрать проект
deploy          - Полный деплой (pull + build + restart)
deploy-quick    - Быстрый деплой (pull + restart)
migrate         - Запустить миграции БД
git-status      - Показать детальный git статус
git-log [lines] - Показать git log (по умолчанию: 20 записей)
disk            - Проверить место на диске
health          - Запустить проверку здоровья сервера
help            - Показать справку
```

---

## Информация о сервере

- **IP адрес**: 81.177.6.46
- **Домен**: 3931b3fe50ab.vps.myjino.ru
- **HTTPS**: https://3931b3fe50ab.vps.myjino.ru
- **Пользователь**: valstan
- **Порт SSH**: 22
- **Проект**: SETKA
- **Директория проекта**: `/home/valstan/SETKA`

### Сервисы

| Сервис | Описание |
|--------|----------|
| `setka` | FastAPI приложение (uvicorn, порт 8000) |
| `setka-celery-worker` | Celery worker (фоновые задачи) |
| `setka-celery-beat` | Celery Beat (расписание задач) |

### База данных

| Компонент | Значение |
|-----------|----------|
| PostgreSQL | localhost:5432, база `setka` |
| Redis | localhost:6379/0 |

---

## Troubleshooting

### SSH подключение не работает
1. Проверьте, что SSH ключ добавлен на VPS
2. Убедитесь, что firewall не блокирует порт 22
3. Проверьте путь к ключу в MCP конфигурации (используйте двойные обратные слеши `\\`)
4. Проверьте вручную: `ssh valstan@81.177.6.46`

### MCP сервер не появляется в VS Code
1. Проверьте синтаксис JSON в конфигурации MCP
2. Перезапустите VS Code полностью
3. Проверьте настройки MCP в вашем AI-расширении

### Команды выполняются долго
- Увеличьте `--timeout` в MCP конфигурации (по умолчанию 120000ms = 2 минуты)
- Для долгих операций используйте `deploy-quick` вместо `deploy`

### AI-агент не может выполнить команду
1. Проверьте логи AI-расширения (обычно есть вкладка Output)
2. Убедитесь что `npx ssh-mcp` работает: `npx -y ssh-mcp --help`
3. Проверьте что vps-manage.sh исполняемый: `ls -la /home/valstan/scripts/vps-manage.sh`

---

## Готово! 🎉

После выполнения шагов выше, ИИ-агент в VS Code сможет управлять VPS напрямую из чата: деплоить, перезапускать, смотреть логи, запускать миграции.
