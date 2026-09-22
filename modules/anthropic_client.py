"""Запасной нейро-движок на Anthropic — тот же контракт, что ``deepseek_client.chat``.

**Почему отдельный модуль, а не ветка внутри DeepSeek-клиента.** Anthropic
**не** OpenAI-совместим: другой эндпоинт (``/v1/messages`` вместо
``/chat/completions``), другая авторизация, системный промпт отдельным полем, а
не сообщением, и ответ — список блоков вместо ``choices``. Втиснуть это в тело
соседнего вызова значит получить функцию, где половина строк под ``if``.
Диспетчер остался один (``deepseek_client.chat``), реализации — две.

**Почему SDK, а не чистый HTTP** — в отличие от DeepSeek, где выбор голого
``urllib`` записан осознанно. Форма запроса Anthropic шире (блоки контента,
``cache_control``, ``stop_reason``, отдельный учёт кэша), её легко написать
неправильно по памяти, и ошибка проявится не отказом, а молча неверным полем.
SDK эту форму гарантирует. Пакет мягкий: импорт ленивый, его отсутствие —
обычный код отказа ``no_sdk``, а не падение импорта у пяти потребителей.

**Префикс-кэш включён явно.** У DeepSeek кэш работает сам, у Anthropic его надо
разметить: ``cache_control`` на системном блоке. Постулаты — ~19.5 КБ,
одинаковые для всех чанков прогона (``render_effective_postulates`` отдаёт базу
байт-в-байт ровно ради этого), и на 700 вызовах в сутки разметка — разница
примерно вдвое по счёту. Пятиминутный TTL волне подходит: волна проходит 57
районов за минуты.

**Чего этот адаптер НЕ делает.** ``json_object`` игнорируется: у Anthropic
строгий JSON включается схемой (``output_config.format``), а ``chat()`` —
общий вызов для пяти разных потребителей, схема у каждого своя. Синтаксис
всё равно разбирается свободным парсером на стороне вызывающего, как и у
DeepSeek: режим провайдера гарантировал синтаксис, а не схему.

``temperature`` тоже игнорируется — и это не упрощение, а форма API.
В ``anthropic`` 1.7.0 у ``messages.create`` параметра ``temperature`` нет
вовсе, и ``**kwargs`` метод не принимает: передача упала бы ``TypeError``
ещё до сети, а широкий ``except`` внизу превратил бы это в «network» на
каждом вызове — отказ, неотличимый от сетевого. Аргумент оставлен в
сигнатуре только ради совместимости с ``deepseek_client.chat``, где
температура работает. Практическое следствие: ответы запасного движка чуть
менее детерминированы, чем у DeepSeek с ``temperature=0.2``.

**Никогда не бросает** — как и DeepSeek-клиент. Все потребители фоновые,
отказ возвращается кодом причины.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from config.anthropic_llm import get_api_key as _cfg_key
from config.anthropic_llm import get_max_tokens as _cfg_max_tokens
from config.anthropic_llm import get_model as _cfg_model
from config.anthropic_llm import get_timeout as _cfg_timeout

logger = logging.getLogger(__name__)


def log_cache_usage(label: str, model: str, usage: Any, stop_reason: str) -> None:
    """Доля префикс-кэша одной строкой — тот же мандат R29, что у DeepSeek.

    Поля Anthropic другие: ``input_tokens`` — это ТОЛЬКО некэшированный ввод,
    а не весь промпт. Полный ввод = ``input_tokens + cache_read + cache_write``,
    и доля кэша считается от него. Сложить ``input_tokens`` с DeepSeek'овым
    ``prompt_tokens`` в одно среднее нельзя — это разные величины.

    ``-`` значит «поля нет» (не «ноль»): различие то же, что у DeepSeek, и по
    той же причине — приёмка, которая их путает, врёт.
    """
    if usage is None:
        return
    fresh = getattr(usage, "input_tokens", None)
    read = getattr(usage, "cache_read_input_tokens", None)
    write = getattr(usage, "cache_creation_input_tokens", None)
    out = getattr(usage, "output_tokens", None)

    total_in = sum(v for v in (fresh, read, write) if isinstance(v, int))
    share = f"{100.0 * read / total_in:.1f}" if isinstance(read, int) and total_in > 0 else "-"

    logger.info(
        "anthropic-usage label=%s model=%s input_fresh=%s cache_read=%s cache_write=%s "
        "hit_pct=%s output=%s stop=%s",
        label,
        model or "-",
        fresh if fresh is not None else "-",
        read if read is not None else "-",
        write if write is not None else "-",
        share,
        out if out is not None else "-",
        stop_reason or "-",
    )


def chat(
    *,
    user: str,
    system: Optional[str] = None,
    temperature: float = 0.2,
    max_tokens: Optional[int] = None,
    json_object: bool = False,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: Optional[float] = None,
    label: str = "anthropic",
) -> Dict[str, Any]:
    """Один диалоговый вызов. Контракт ответа — как у ``deepseek_client.chat``.

    Возвращает ``{ok, content, model, usage}`` либо ``{ok: False, reason, detail?}``.
    Коды отказа те же плюс один новый: ``no_sdk`` | ``no_api_key`` |
    ``empty_prompt`` | ``network`` | ``http_<код>`` | ``truncated`` |
    ``empty_response``.

    ``truncated`` — это ``stop_reason == "max_tokens"``, ровно тот же смысл,
    что ``finish_reason == "length"`` у DeepSeek, и проверяется так же РАНЬШЕ
    пустоты: обрезанный ответ бывает и непустым (оборванный JSON), и пустым.
    Слитые в одну причину, они стоили соседнему проекту 47 % вердиктов за две
    недели (G334, письмо brain 2026-09-10).
    """
    try:
        import anthropic  # ленивый импорт: отсутствие пакета — отказ, не падение
    except ImportError:
        return {"ok": False, "reason": "no_sdk", "detail": "pip install anthropic"}

    key = api_key if api_key is not None else _cfg_key()
    if not key:
        return {"ok": False, "reason": "no_api_key"}
    if not (user or "").strip():
        return {"ok": False, "reason": "empty_prompt"}

    name = model or _cfg_model()
    budget = max_tokens if max_tokens is not None else _cfg_max_tokens()

    kwargs: Dict[str, Any] = {
        "model": name,
        "max_tokens": budget,
        "messages": [{"role": "user", "content": user}],
    }
    if system and system.strip():
        # cache_control на системном блоке — единственное место, где включается
        # префикс-кэш. Постулаты стабильны байт-в-байт, на них он и рассчитан.
        kwargs["system"] = [
            {
                "type": "text",
                "text": system,
                "cache_control": {"type": "ephemeral"},
            }
        ]
    client = anthropic.Anthropic(
        api_key=key,
        timeout=timeout if timeout is not None else _cfg_timeout(),
    )

    try:
        response = client.messages.create(**kwargs)
    except anthropic.APIStatusError as e:
        # Код отдаём сырым, как у DeepSeek: 401 чинит человек, 429 проходит сам,
        # 402 — оплата. Различать их вызывающему нужнее, чем «просто ошибка».
        detail = str(getattr(e, "message", "") or e)[:300]
        return {"ok": False, "reason": f"http_{e.status_code}", "detail": detail}
    except anthropic.APIConnectionError as e:  # APITimeoutError — его подкласс
        return {"ok": False, "reason": "network", "detail": f"{type(e).__name__}: {e}"[:300]}
    except Exception as e:  # pragma: no cover — движок не вправе ронять волну
        return {"ok": False, "reason": "network", "detail": f"{type(e).__name__}: {e}"[:300]}

    stop_reason = str(getattr(response, "stop_reason", "") or "")
    usage = getattr(response, "usage", None)
    try:
        log_cache_usage(label, name, usage, stop_reason)
    except Exception:  # pragma: no cover — учёт не важнее уже оплаченного ответа
        logger.debug("anthropic-usage: не удалось записать метрику", exc_info=True)

    if stop_reason == "max_tokens":
        return {"ok": False, "reason": "truncated", "detail": f"max_tokens={budget}"}

    parts: List[str] = []
    for block in getattr(response, "content", None) or []:
        # Блоки бывают не только текстовые (thinking и пр.) — берём только text.
        if getattr(block, "type", None) == "text":
            parts.append(str(getattr(block, "text", "") or ""))
    content = "".join(parts).strip()
    if not content:
        return {"ok": False, "reason": "empty_response"}

    return {"ok": True, "content": content, "model": name, "usage": usage}
