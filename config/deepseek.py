"""Конфиг DeepSeek — движка классификации контент-конвейера (решение владельца 2026-08-09).

Почему прямой API, а не облачная рутина: рутина (Claude Code в облаке) — это
человекоуправляемый инструмент с прогонами по расписанию сессий, а конвейеру
нужен headless-вызов из Celery по cron'у, без включённого компьютера владельца.
Ровно тот довод, которым ADR-0003 §A выбирал API против рутин; тогда путь через
рутину взяли как «сменный мотор», пока нет ключа.

Почему DeepSeek, а не Haiku: решение владельца 2026-08-09. Оно же фактически
снимает вопрос пилота П4 (DeepSeek vs Haiku, мандат brain 2026-08-01) — сравнение
делалось ради этого выбора, и выбор сделан. Числа по качеству всё равно нужны, но
теперь как метрика shadow-фазы конвейера, а не как отдельный оффлайн-эксперимент.

**Ключ живёт в комнате КАРМАНа, не на прод-боксе** (решение владельца 2026-08-09).
Комната для того и заводилась, чтобы не носить секреты руками на серверы:
владелец кладёт ``DEEPSEEK_API_KEY`` в свою комнату через UI КАРМАНа, а
``modules/secrets_bootstrap`` подтягивает его при старте — и веб-процесса, и
Celery-воркера (bootstrap вызывается в обоих: ``main.py`` и ``tasks/celery_app.py``).
Имя ключа занесено в allowlist; без этого vault-клиент его молча проигнорирует.

Env vars:
  DEEPSEEK_API_KEY            # секрет (из комнаты КАРМАНа)
  DEEPSEEK_MODEL=deepseek-chat
  DEEPSEEK_BASE_URL=https://api.deepseek.com
  DEEPSEEK_TIMEOUT=60         # секунды на один вызов
  DEEPSEEK_MAX_TOKENS=1200    # потолок ответа
"""

from __future__ import annotations

import os

DEFAULT_BASE_URL = "https://api.deepseek.com"
# deepseek-chat — обычная модель; deepseek-reasoner дороже и медленнее, а для
# «рубрика + заголовок + лёгкая редактура» рассуждающая модель не нужна.
DEFAULT_MODEL = "deepseek-chat"


def get_api_key() -> str:
    """Ключ DeepSeek. Пусто = движок не настроен (конвейер отработает отказом, не падением).

    Читаем из окружения при каждом вызове, а не при импорте: ротация ключа в
    комнате КАРМАНа не должна требовать рестарта процесса больше, чем это нужно
    bootstrap'у.
    """
    return (os.getenv("DEEPSEEK_API_KEY") or "").strip()


def get_model() -> str:
    """Имя модели (env ``DEEPSEEK_MODEL``)."""
    return (os.getenv("DEEPSEEK_MODEL") or "").strip() or DEFAULT_MODEL


def get_base_url() -> str:
    """База API без хвостового слэша (env ``DEEPSEEK_BASE_URL``)."""
    raw = (os.getenv("DEEPSEEK_BASE_URL") or "").strip() or DEFAULT_BASE_URL
    return raw.rstrip("/")


def get_timeout() -> float:
    """Таймаут одного вызова в секундах. Границы 5..300, дефолт 60."""
    try:
        return float(max(5, min(300, int(os.getenv("DEEPSEEK_TIMEOUT", "60")))))
    except ValueError:
        return 60.0


def get_max_tokens() -> int:
    """Потолок ответа в токенах. Границы 200..4000, дефолт 1200.

    Ответ — JSON с рубрикой, заголовком и отредактированным текстом; 1200 хватает
    на пост средней длины. Потолок здесь — защита от счёта, а не от многословия:
    длину текста ограничивает промпт.
    """
    try:
        return max(200, min(4000, int(os.getenv("DEEPSEEK_MAX_TOKENS", "1200"))))
    except ValueError:
        return 1200
