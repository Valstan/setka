"""Маскирование секретов в логах — process-wide, через фабрику LogRecord.

**Зачем.** Инцидент 2026-08-12: оба Telegram-бота (`@malm_info_bot`,
`@valstan_bot`) угнаны — переименованы в рекламу казино, на них поставлен
чужой вебхук, входящие апдейты уходили на сторонний сервер. Один из
подтверждённых путей утечки токена нашёлся в наших же логах:

    logger.warning(f"telegram send failed: {e}")

где ``e`` — исключение ``requests``, а его текст содержит **полный URL**::

    Max retries exceeded with url: /bot<ТОКЕН>/sendMessage

Bot API кладёт секрет в путь URL, и любое сетевое исключение делает токен
частью текста ошибки. В ротированных логах прода нашлось 9 таких вхождений,
файлы — с правами 644 в каталоге 775.

**Почему фабрика записей, а не фильтр на хендлере.** Фильтр надо вешать на
каждый хендлер, а хендлеры в процессе не только наши: uvicorn ставит свои
(`uvicorn.access`/`uvicorn.error`), Celery переинициализирует логгеры на старте
worker'а, библиотеки заводят собственные. Любой пропущенный хендлер = дыра, и
дыра молчаливая. ``logging.setLogRecordFactory`` перехватывает записи **до**
того, как они куда-либо попадут, и покрывает весь процесс одним вызовом,
независимо от того, кто владеет логгером.

Трейсбеки маскируются там же: фабрика получает ``exc_info`` и заранее кладёт
отредактированный текст в ``record.exc_text``, а ``logging.Formatter`` при
форматировании переиспользует уже заполненный ``exc_text`` и повторно
исключение не разворачивает.

**Два слоя маскирования, и оба нужны:**

1. **По значению** — точные значения секретов из окружения (``VK_TOKEN_*``,
   ``TELEGRAM_TOKEN_*``, ``*_KEY``, ``*_SECRET``, пароль в ``DATABASE_URL``).
   Ловит секрет в любом виде и в любом формате, включая тот, для которого мы не
   написали регулярку.
2. **По форме** — регулярки на узнаваемые форматы (токен Bot API, ``vk1.a.*``,
   ``?access_token=``, ``Bearer``, пароль в URL). Ловит **чужие** секреты,
   которых нет в нашем окружении: токен из ответа API, ключ из тела запроса,
   значение, которое ещё не завели в env.

Ни один слой не заменяет другой: первый не знает форматов, второй не знает
значений.

Функция идемпотентна: повторный ``install_log_redaction()`` не наслаивает
фабрики (проверяется маркер на текущей фабрике).
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Iterable, List, Pattern, Tuple

# ─── Слой 2: маскирование по форме ───────────────────────────────────────────

# Токен Bot API: <bot_id>:<секрет>. Числовой id — публичная часть (он и так
# виден в любом упоминании бота), поэтому оставляем его: по нему в инциденте
# опознаётся, КАКОЙ бот засветился, а секрет при этом не восстановим.
#
# NB: id сохраняется, только когда сработал этот слой. Для НАШИХ токенов первым
# отрабатывает слой «по значению» — он знает значение целиком, вместе с id, и
# закрывает всю строку. То есть в прод-логе будет `/bot[REDACTED]/sendMessage`
# без номера (проверено на приёмке 2026-08-12), а id остаётся видимым для
# чужих токенов, которых нет в нашем окружении. Так и задумано: полное
# закрытие своего секрета важнее удобства чтения.
#
# Опциональный литерал ``bot`` в начале — не украшение. Главная форма утечки —
# путь URL ``/bot<ТОКЕН>/sendMessage``, и там перед цифрами стоит буква ``t``:
# границы слова ``\b`` между ``t`` и ``5`` нет, поэтому шаблон без этой
# альтернативы не срабатывал ровно на том тексте, из-за которого всё и
# случилось (поймано тестом test_url_with_token_in_path_redacted).
_TELEGRAM_TOKEN_RE = re.compile(r"\b(bot)?(\d{6,12}):[A-Za-z0-9_-]{30,}")
# VK access token нового формата.
_VK_TOKEN_RE = re.compile(r"\bvk1\.a\.[A-Za-z0-9_-]{20,}")
# Секрет в query-строке: ?access_token=..., &api_key=..., &key=...
_QUERY_SECRET_RE = re.compile(
    r"(?i)\b(access_token|api_key|apikey|auth_token|token|secret|password|pwd|key)"
    r"=([^&\s\"'<>]{6,})"
)
# Authorization: Bearer <...>
_BEARER_RE = re.compile(r"(?i)\b(Bearer|Basic)\s+([A-Za-z0-9._\-+/=]{8,})")
# Пароль внутри URL подключения: postgres://user:pass@host, redis://:pass@host
_URL_CRED_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.\-]*)://([^:/\s@]*):([^@/\s]+)@")

REDACTED = "[REDACTED]"

_PATTERNS: Tuple[Tuple[Pattern[str], str], ...] = (
    # Несработавшая группа ``bot`` подставляется пустой строкой (re.sub, 3.5+).
    (_TELEGRAM_TOKEN_RE, r"\1\2:" + REDACTED),
    (_VK_TOKEN_RE, "vk1.a." + REDACTED),
    (_QUERY_SECRET_RE, r"\1=" + REDACTED),
    (_BEARER_RE, r"\1 " + REDACTED),
    (_URL_CRED_RE, r"\1://\2:" + REDACTED + "@"),
)

# ─── Слой 1: маскирование по значению ────────────────────────────────────────

# Имена, чьи значения из окружения считаем секретами. Префиксы/суффиксы, а не
# точный список: новый VK_TOKEN_<NAME> или <SITE>_INGEST_KEY попадает под
# маскирование сам, без правки этого файла (тот же приём, что в allowlist'е
# modules/secrets_bootstrap.py).
_SECRET_NAME_PARTS: Tuple[str, ...] = (
    "TOKEN",
    "SECRET",
    "PASSWORD",
    "API_KEY",
    "_KEY",
    "KEY_",
    "DATABASE_URL",
    "REDIS_URL",
)
# Короткие значения не маскируем: "0", "on", имя бота в TELEGRAM_TOKEN_NAME-
# подобной переменной. Маскировать трёхбуквенное значение — значит изрешетить
# каждую строку лога, где эти буквы встретились случайно.
_MIN_SECRET_LEN = 12
# Значения-конфиги, которые под правило имени подходят, но секретами не
# являются. Держим явным списком: молчаливое исключение по эвристике («похоже
# на список кодов») пропустит настоящий секрет ровно того же вида.
_NAME_EXACT_ALLOWED: frozenset = frozenset(
    {
        "VK_PUBLISH_TOKEN_NAME",
        "VK_PUBLISH_TOKEN_NAMES",
        "VK_RESERVE_PUBLISH_TOKEN_NAMES",
        "VK_NEVER_PUBLISH_TOKEN_NAMES",
        "RADAR_BOT_NAME",
        "SECRETS_VAULT_URL",
        "TG_PREVIEW_RELAY_URL",
    }
)


def _is_secret_name(name: str) -> bool:
    upper = name.upper()
    if upper in _NAME_EXACT_ALLOWED:
        return False
    return any(part in upper for part in _SECRET_NAME_PARTS)


def collect_secret_values(env: Any = None) -> List[str]:
    """Значения секретов из окружения — длинные вперёд.

    Порядок важен: если один секрет является префиксом другого (или URL
    подключения содержит пароль как подстроку), замена короткого первым
    разрежет длинный на куски и оставит хвост в логе. Сортировка по убыванию
    длины закрывает это без анализа вложенности.
    """
    env = env if env is not None else os.environ
    values = {
        value.strip()
        for name, value in env.items()
        if _is_secret_name(name) and value and len(value.strip()) >= _MIN_SECRET_LEN
    }
    return sorted(values, key=len, reverse=True)


def redact(text: str, secret_values: Iterable[str] = ()) -> str:
    """Замаскировать секреты в строке: сперва по значению, затем по форме."""
    if not text:
        return text
    for value in secret_values:
        if value in text:
            text = text.replace(value, REDACTED)
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_arg(arg: Any, secret_values: List[str]) -> Any:
    """Аргумент %-форматирования: маскируем ЛЮБОЙ, чья строковая форма несёт секрет.

    **Не-строки трогать обязательно** (инцидент 2026-08-19). Прежняя версия
    пропускала всё, кроме ``str`` и исключений, под доводом «само сообщение
    маскируется целиком». Довод неверен: маскируются ``record.msg`` и аргументы
    по отдельности, а склейка происходит позже, в ``record.getMessage()`` — уже
    после фабрики. Объект, чей ``__str__`` содержит секрет, проходил насквозь.

    Так и утёк VK-токен: ``httpx`` логирует ``logger.info("HTTP Request: %s %s",
    request.method, request.url)``, где ``request.url`` — объект ``httpx.URL``, а
    VK кладёт ``access_token`` в query. Слой «по значению» его тоже не поймал:
    community-токены живут в БД, а не в окружении, и их значений фабрика не
    знает. Оба слоя промахнулись одновременно — 2602 строки в celery-worker.log.

    Тот же класс, что G239 (секрет в пути URL уходит в лог через текст
    исключения): секрет попадает в лог не строкой, а объектом, который станет
    строкой позже.

    Числа, ``None`` и прочее, чья строковая форма секрета не содержит,
    возвращаются как есть — иначе ``%d`` получил бы ``str`` и сломал формат.
    """
    if isinstance(arg, str):
        return redact(arg, secret_values)
    if isinstance(arg, BaseException):
        # Исключение форматируется как str(e) — маскируем его текст, сохранив
        # тип: "ConnectionError: ... [REDACTED] ..." читается, а токена нет.
        return f"{type(arg).__name__}: {redact(str(arg), secret_values)}"
    try:
        raw = str(arg)
    except Exception:  # noqa: BLE001 — логирование не должно падать никогда
        return arg
    cleaned = redact(raw, secret_values)
    # Подменяем только когда маскирование ЧТО-ТО изменило: иначе int останется
    # int'ом и ``%d`` продолжит работать.
    return cleaned if cleaned != raw else arg


_MARKER = "_setka_log_redaction"


def install_log_redaction(env: Any = None) -> bool:
    """Поставить маскирующую фабрику LogRecord на весь процесс.

    Возвращает ``True``, если фабрика установлена сейчас, и ``False``, если она
    уже стояла (повторный вызов безопасен — main.py и celery_app.py зовут её
    независимо, а Celery ещё и переинициализирует логгеры на ``worker_ready``).

    Значения секретов снимаются в момент установки. Вызывать **после**
    ``bootstrap_secrets()``: секреты из комнаты КАРМАНа попадают в окружение
    именно там, и до вызова их в ``os.environ`` ещё нет.
    """
    current = logging.getLogRecordFactory()
    if getattr(current, _MARKER, False):
        return False

    secret_values = collect_secret_values(env)

    def factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = current(*args, **kwargs)
        if isinstance(record.msg, str):
            record.msg = redact(record.msg, secret_values)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: _redact_arg(v, secret_values) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(_redact_arg(a, secret_values) for a in record.args)
        # Трейсбек: заполняем exc_text заранее — Formatter переиспользует уже
        # готовое значение и повторно исключение не разворачивает.
        if record.exc_info and not record.exc_text:
            try:
                formatted = logging.Formatter().formatException(record.exc_info)
                record.exc_text = redact(formatted, secret_values)
            except Exception:  # noqa: BLE001 — логирование не должно падать никогда
                record.exc_text = "[traceback suppressed by log redaction]"
        return record

    setattr(factory, _MARKER, True)
    logging.setLogRecordFactory(factory)
    return True


def _reset_for_tests(previous: Callable[..., logging.LogRecord]) -> None:
    """Вернуть фабрику (только для тестов — переустановка глобального стейта)."""
    logging.setLogRecordFactory(previous)
