# 🧪 Тестирование SETKA

Проект использует два типа тестов: **unit-тесты** и **интеграционные тесты**.

---

## 📋 Структура тестов

### Unit-тесты (безопасные для CI/CD)

Расположены в папке `tests/` и **не требуют** подключения к PostgreSQL, Redis или VK API:

| Директория | Что тестирует | Статус |
|------------|---------------|--------|
| `tests/test_config/` | Конфигурация (runtime.py, env vars parsing) | ✅ Созданы |
| `tests/test_filters/` | Модульная система фильтрации | ✅ Созданы |
| `tests/test_deduplication/` | Дедупликация (LIP, media fingerprints) | ✅ Созданы |
| `tests/test_publisher/` | Публикация (VK, Telegram) | 🔄 В разработке |
| `tests/test_vk_monitor/` | VK мониторинг и парсинг | 🔄 В разработке |
| `tests/test_aggregation/` | Агрегация контента | 🔄 В разработке |
| `tests/test_api/` | API endpoints (FastAPI) | 🔄 В разработке |
| `tests/test_tasks/` | Celery задачи | 🔄 В разработке |
| `tests/test_scheduler/` | Планировщик контента | 🔄 В разработке |
| `tests/test_notifications/` | Система уведомлений | 🔄 В разработке |

**Запуск unit-тестов:**
```bash
pytest tests/ -v           # подробный вывод
pytest tests/ -q           # тихий режим (для CI/CD)
pytest tests/ --cov=modules --cov=tasks  # с покрытием
```

### Интеграционные тесты (требуют реального окружения)

Расположены в `scripts/test_*.py` и требуют working database + внешних API:

| Скрипт | Что тестирует | Требует |
|--------|---------------|---------|
| `scripts/test_vk_monitor.py` | VK мониторинг | БД, VK токены |
| `scripts/test_publisher.py` | VK Publisher | БД, VK токены |
| `scripts/test_full_workflow.py` | Полный workflow | БД, VK, Redis, Groq |
| `scripts/test_deduplication.py` | Дедупликация | БД |
| `scripts/test_notifications_system.py` | Уведомления | БД, Redis, Telegram |
| ... и другие | ... | ... |

⚠️ **Не запускать в CI/CD!** Эти скрипты дорогие по времени и требуют реальных сервисов.

---

## 🎯 Тестовый полигон (Test Polygon)

Для безопасного тестирования публикаций используется **тестовая группа VK**:

- **ID:** `VK_TEST_GROUP_ID` (из env vars)
- **Пример:** `-137760500`

Все публикации можно направить в тестовую группу вместо рабочих, установив:

```env
VK_TEST_GROUP_ID=-137760500
```

Или используя соответствующие параметры в API вызовах.

---

## 🚀 Примеры использования

### Сценарий 1: Разработка нового фильтра

```bash
# 1. Пишешь код нового фильтра в modules/filters/my_filter.py

# 2. Пишешь unit-тест
cat > tests/test_filters/test_my_filter.py << 'EOF'
import pytest
from modules.filters.my_filter import MyFilter

def test_my_filter_blocks_ads():
    f = MyFilter()
    assert f.is_ad("Купить недорого!") == True
    assert f.is_ad("Сегодня в городе прошёл митинг") == False

def test_my_filter_empty_input():
    f = MyFilter()
    assert f.is_ad("") == False
EOF

# 3. Запускаешь тесты
pytest tests/test_filters/test_my_filter.py -v

# 4. Если всё ок — коммит
git add modules/filters/my_filter.py tests/test_filters/test_my_filter.py
git commit -m "Add my_filter with unit tests"
git push
```

### Сценарий 2: Отладка публикации

```bash
# Используем интеграционный скрипт (требует working окружения)
cd /home/valstan/SETKA
source venv/bin/activate
python scripts/test_publisher.py
```

### Сценарий 3: CI/CD проверка

```bash
# GitHub Actions автоматически:
# 1. Запустит pytest tests/ -q (unit-тесты)
# 2. Проверит форматирование (black, isort, flake8)
# 3. Выдаст ✅ или ❌
# Нет токенов, нет БД, очень быстро (~10 сек) ✅
```

---

## 🔧 Как написать unit-тест для SETKA

### Принцип: мокать всё внешнее

```python
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

# Пример: тестирование фильтра без БД
from modules.filters.ads_filter import AdsFilter

class TestAdsFilter:
    def setup_method(self):
        self.filter = AdsFilter()

    def test_detects_obvious_ad(self):
        text = "Продаю гараж недорого! Звоните: +7-999-123-45-67"
        assert self.filter.is_advertisement(text) == True

    def test_allows_news(self):
        text = "Сегодня в городе состоялось открытие новой школы"
        assert self.filter.is_advertisement(text) == False

    def test_empty_text(self):
        assert self.filter.is_advertisement("") == False

    def test_none_text(self):
        assert self.filter.is_advertisement(None) == False
```

### Пример: тестирование async функции с моком БД

```python
import pytest
from unittest.mock import AsyncMock, patch

@pytest.mark.asyncio
async def test_get_region_by_code():
    """Test fetching region from database."""
    from database.models import Region

    # Мок сессии БД
    mock_session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalars().first.return_value = Region(
        id=1, code="mi", name="МАЛМЫЖ - ИНФО", is_active=True
    )
    mock_session.execute = AsyncMock(return_value=mock_result)

    # Тестируем функцию с моком
    with patch("database.connection.get_db_session", return_value=mock_session):
        # Здесь должен быть вызов вашей функции
        # result = await get_region_by_code("mi")
        # assert result.code == "mi"
        pass  # Заменить на реальный вызов
```

### Пример: тестирование Celery задачи с моком VK API

```python
import pytest
from unittest.mock import patch, MagicMock

def test_vk_monitor_task():
    """Test VK monitoring task with mocked VK API."""
    from unittest.mock import AsyncMock

    # Мок VK API клиента
    mock_vk_client = MagicMock()
    mock_vk_client.method.return_value = {
        "items": [
            {"id": 1, "text": "News post", "date": 1234567890, "views": 100}
        ],
        "count": 1
    }

    # Мок БД
    with patch("vk_api.VkApi", return_value=mock_vk_client):
        with patch("database.connection.get_db_session"):
            # Здесь вызов задачи
            # from tasks.parsing_tasks import run_vk_monitoring
            # result = run_vk_monitoring("mi")
            # assert result["posts_found"] == 1
            pass  # Заменить на реальный вызов
```

---

## ✅ CI/CD Pipeline

**GitHub Actions** (`.github/workflows/ci.yml`):
- Запускается на `push` и `pull_request` в `main`
- Шаги:
  1. ✅ Проверка синтаксиса Python
  2. ✅ black — проверка форматирования
  3. ✅ isort — проверка импортов
  4. ✅ flake8 — линтинг
  5. ✅ `pytest tests/ -q` — unit-тесты
- Python 3.12
- **Результат:** ✅ Быстро (~15 сек), безопасно (нет токенов/БД), надёжно

---

## 📊 Покрытие тестами (цели)

| Модуль | Текущее покрытие | Целевое покрытие | Приоритет |
|--------|------------------|-------------------|-----------|
| `modules/filters/` | 🟡 Частичное | 🟢 90%+ | 🔴 Критический |
| `modules/deduplication/` | 🟡 Частичное | 🟢 90%+ | 🔴 Критический |
| `modules/publisher/` | 🔴 Нет | 🟢 85%+ | 🔴 Критический |
| `modules/vk_monitor/` | 🔴 Нет | 🟢 80%+ | 🟡 Высокий |
| `modules/aggregation/` | 🔴 Нет | 🟢 80%+ | 🟡 Высокий |
| `modules/ai_analyzer/` | 🔴 Нет | 🟢 75%+ | 🟡 Высокий |
| `modules/scheduler/` | 🔴 Нет | 🟢 80%+ | 🟡 Высокий |
| `web/api/` | 🔴 Нет | 🟢 80%+ | 🟢 Средний |
| `tasks/` | 🔴 Нет | 🟢 75%+ | 🟢 Средний |
| `config/runtime.py` | 🟢 Есть | 🟢 90%+ | 🟡 Высокий |
| `utils/` | 🔴 Нет | 🟢 80%+ | 🟢 Средний |

---

## ⚡ Быстрые команды

```bash
# Все unit-тесты
pytest tests/

# Один конкретный файл
pytest tests/test_filters/test_ads_filter.py -v

# Один конкретный тест
pytest tests/test_filters/test_ads_filter.py::TestAdsFilter::test_detects_obvious_ad -v

# С покрытием
pytest tests/ --cov=modules --cov-report=html

# Тесты по метке
pytest tests/ -m "not slow"

# Только failed тесты (после исправлений)
pytest tests/ --lf

# Подробный трейсбэк
pytest tests/ -vv
```

---

## 📋 Правила написания тестов

1. **Один тест — одна проверка**
2. **Название теста** должно описывать что проверяется: `test_<function>_<scenario>_<expected>`
3. **Использовать моки** для всех внешних зависимостей (БД, API, файлы)
4. **Тестировать краевые случаи**: пустые данные, None, ошибки, лимиты
5. **Не зависеть от порядка** запуска тестов
6. **Быстрые тесты** — маркировка `@pytest.mark.fast`
7. **Медленные тесты** — маркировка `@pytest.mark.slow`

---

**Последнее обновление:** 2026-04-08
**Ответственный:** AI-ассистент SETKA
**Статус:** ✅ Инфраструктура создана, тесты добавляются
