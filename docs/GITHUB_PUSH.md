# Пуш на GitHub

## Вариант 1: Через веб-интерфейс (рекомендуется)

1. Откройте https://github.com/new
2. Создайте новый репозиторий:
   - **Name:** `setka`
   - **Description:** `SETKA - Multimedia Management System for News Resources`
   - **Visibility:** Private (рекомендуется, т.к. содержит структуру проекта)
   - **НЕ** добавляйте README, .gitignore, license (у нас уже есть)

3. После создания выполните:

```bash
cd /home/valstan/SETKA
git remote add origin https://github.com/Valstan/setka.git
git push -u origin main
```

При запросе введите ваш GitHub username и personal access token.

---

## Вариант 2: С помощью скрипта (автоматически)

Я подготовил команды ниже, но вам понадобится GitHub Personal Access Token.

### Получить Personal Access Token:

1. Откройте: https://github.com/settings/tokens
2. Нажмите "Generate new token (classic)"
3. Выберите scopes:
   - ✅ repo (полный доступ к репозиториям)
4. Сгенерируйте и скопируйте токен

### Затем выполните:

```bash
cd /home/valstan/SETKA

# Замените YOUR_TOKEN на ваш token
export GITHUB_TOKEN="YOUR_TOKEN"

# Создать репозиторий
curl -H "Authorization: token $GITHUB_TOKEN" \
  -d '{"name":"setka","description":"SETKA - Multimedia Management System for News Resources","private":true}' \
  https://api.github.com/user/repos

# Добавить remote
git remote add origin https://github.com/Valstan/setka.git

# Запушить
git push -u origin main
```

---

## Что будет запушено:

✅ **Код:** 28 Python файлов
✅ **Документация:** 9 markdown файлов
✅ **Конфигурация:** .gitignore, requirements.txt, .env.example
✅ **Скрипты:** 10 утилит
✅ **API:** FastAPI роутеры
✅ **Анализ старого проекта:** JSON файлы

❌ **НЕ будет запушено (защищено .gitignore):**
- config/config.secure.py (токены и пароли)
- venv/ (виртуальное окружение)
- __pycache__/ (Python cache)
- logs/ (логи)
- backup/ (бэкапы БД) - **ВКЛЮЧЕН** в коммит для примера
- old_project_analysis/postopus/ (старый проект)

---

## После пуша:

Ваш проект будет доступен по адресу:
**https://github.com/Valstan/setka**

Вы сможете:
- Просматривать код онлайн
- Клонировать на другие сервера
- Отслеживать изменения
- Создавать branches для экспериментов

