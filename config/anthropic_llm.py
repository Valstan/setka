"""Конфиг запасного нейро-движка (Anthropic) — подпорка на время отказа DeepSeek.

**Почему вообще второй движок.** 2026-09-22 в 12:22 DeepSeek начал отвечать
``402 Payment Required``; за два часа 160 отказов, каждая волна уходила «по
обычным фильтрам» — сеть публиковала без нейро-отбора и ни один сервис при этом
не падал. Тихая деградация: сигнал есть только в логе. Решение владельца в тот
же день — завести переключаемый запасной провайдер, чтобы пауза в оплате одного
поставщика не снимала нейро-фильтр со всей сети.

**Это отклонение от D-024** (мандат brain 2026-08-10: движок экосистемы —
DeepSeek напрямую). Отклонение сознательное и временное; радиус — запись
[P076] реестра. Дефолт ``LLM_PROVIDER`` остаётся ``deepseek``: без явного
переключения ничего не меняется.

**Почему Haiku 4.5 дефолтом, а не Sonnet 5.** Замер 2026-09-22 по журналу:
~700 вызовов и 7.4 млн токенов промпта в сутки. На Sonnet 5 это ~$420/мес с
префикс-кэшем, на Haiku 4.5 — ~$220/мес. Задача («тема + действие + короткое
обоснование») рассуждающей модели не требует. Выбор владельца: Haiku дефолтом,
Sonnet одной переменной.

**Ключ живёт в комнате КАРМАНа**, как ``DEEPSEEK_API_KEY`` (решение владельца
2026-08-09): имя внесено в allowlist ``modules/secrets_bootstrap``, иначе
vault-клиент молча его проигнорирует — ровно так ключ DeepSeek не доехал до
воркера 2026-08-17.

Env vars:
  LLM_PROVIDER=deepseek       # deepseek (дефолт) | anthropic — общий переключатель
  ANTHROPIC_API_KEY           # секрет (из комнаты КАРМАНа)
  ANTHROPIC_MODEL=claude-haiku-4-5
  ANTHROPIC_TIMEOUT=60        # секунды на один вызов
  ANTHROPIC_MAX_TOKENS=1200   # потолок ответа
"""

from __future__ import annotations

import os

PROVIDER_DEEPSEEK = "deepseek"
PROVIDER_ANTHROPIC = "anthropic"

# Haiku 4.5 — самая дешёвая текущая модель ($1/$5 за млн), контекст 200K.
# Для пачки из 10 постов с постулатами (~10.6 тыс. токенов промпта) запас
# четырёхкратный.
DEFAULT_MODEL = "claude-haiku-4-5"

# Sampling-параметров (`temperature`, `top_p`, `top_k`) здесь НЕТ намеренно:
# в SDK 1.7.0 у `messages.create` такого аргумента не существует вовсе, а
# `**kwargs` метод не принимает — попытка передать температуру падает
# `TypeError` ещё до сети. Проверено на установленном пакете 2026-09-22.
# У DeepSeek температура остаётся; расхождение описано в
# ``modules/anthropic_client.chat``.


def get_provider() -> str:
    """Какой движок обслуживает ``chat()``. Дефолт — ``deepseek`` (D-024).

    Читаем при каждом вызове, а не при импорте: переключение провайдера в
    аварии не должно требовать рестарта воркера больше, чем это нужно
    bootstrap'у секретов.
    """
    raw = (os.getenv("LLM_PROVIDER") or "").strip().lower()
    return PROVIDER_ANTHROPIC if raw == PROVIDER_ANTHROPIC else PROVIDER_DEEPSEEK


def get_api_key() -> str:
    """Ключ Anthropic. Пусто = движок не настроен (отказ, а не падение)."""
    return (os.getenv("ANTHROPIC_API_KEY") or "").strip()


def get_model() -> str:
    """Имя модели (env ``ANTHROPIC_MODEL``). Дефолт — Haiku 4.5."""
    return (os.getenv("ANTHROPIC_MODEL") or "").strip() or DEFAULT_MODEL


def get_timeout() -> float:
    """Таймаут одного вызова в секундах. Границы 5..300, дефолт 60 — как у DeepSeek."""
    try:
        return float(max(5, min(300, int(os.getenv("ANTHROPIC_TIMEOUT", "60")))))
    except ValueError:
        return 60.0


def get_max_tokens() -> int:
    """Потолок ответа в токенах. Границы 200..4000, дефолт 1200 — как у DeepSeek."""
    try:
        return max(200, min(4000, int(os.getenv("ANTHROPIC_MAX_TOKENS", "1200"))))
    except ValueError:
        return 1200
