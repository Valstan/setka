"""Общий клиент DeepSeek — один HTTP-вызов на всю экосистему (D-024).

Мандат brain 2026-08-10 (решение владельца D-024): нейро-движок экосистемы —
DeepSeek API напрямую. Всё, что ходило в Groq или в облачную рутину, переводится
сюда тем же паттерном, что конвейер: ключ из комнаты КАРМАНа, прямой вызов.

**Почему отдельный модуль, а не копия вызова в каждом потребителе.** Первый
потребитель (``modules/conveyor/classify.py``) нёс собственный ``_call_api``;
второй и третий скопировали бы его вместе с обработкой ошибок, таймаутом и
разбором тела. Дальше они расходятся молча — ровно тот класс, из-за которого в
проекте сводили VK-токены к единому источнику. Здесь один вызов, один набор
кодов отказа, одно место для правки.

**Чистый HTTP вместо SDK** — тот же выбор, что в конвейере и в доставке: тело
запроса OpenAI-совместимо и умещается в несколько строк, а лишняя зависимость в
рантайме — это то, что придётся обновлять и чинить. Groq-SDK, который отсюда
убран, добавлял к каждому потребителю ветку «а вдруг пакет не установлен».

**Никогда не бросает.** Все потребители — фоновые рутины и UI-хелперы, где
падение вызова не должно ронять вызывающего. Отказ возвращается кодом причины.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from config.deepseek import get_api_key, get_base_url, get_max_tokens, get_model, get_timeout

logger = logging.getLogger(__name__)


def log_cache_usage(label: str, response: Dict[str, Any]) -> None:
    """Записать долю префикс-кэша одной строкой (мандат brain 2026-08-30, R29).

    DeepSeek кэширует СОВПАВШЕЕ НАЧАЛО запроса и считает его по сниженной цене.
    Включать нечего — кэш работает сам; но работает он только пока префикс
    байт-в-байт стабилен, а сломать эту стабильность может правка, которая
    выглядит безобидно: переставленный ключ словаря, новая строка с датой,
    сортировка без вторичного ключа.

    **Отказ здесь бесшумный.** Ответы модели остаются правильными, вызовы
    проходят, в логах ни одной ошибки — меняется только счёт. Поэтому доля
    кэша логируется на каждом вызове: это единственный сигнал, по которому
    видно, что префикс поехал.

    ``hit=- miss=-`` означает, что провайдер полей НЕ ВЕРНУЛ, и это не то же
    самое, что ``hit=0``. Первое — «померить нечем», второе — «кэш не сработал».
    Сложить их в один ноль значит получить приёмку, которая врёт.
    """
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return

    hit = usage.get("prompt_cache_hit_tokens")
    miss = usage.get("prompt_cache_miss_tokens")
    prompt = usage.get("prompt_tokens")

    if isinstance(hit, int) and isinstance(miss, int) and (hit + miss) > 0:
        share = f"{100.0 * hit / (hit + miss):.1f}"
    else:
        share = "-"

    logger.info(
        "deepseek-usage label=%s model=%s prompt=%s hit=%s miss=%s hit_pct=%s completion=%s",
        label,
        response.get("model") or "-",
        prompt if prompt is not None else "-",
        hit if hit is not None else "-",
        miss if miss is not None else "-",
        share,
        usage.get("completion_tokens") if usage.get("completion_tokens") is not None else "-",
    )


def call_api(
    body: Dict[str, Any],
    *,
    api_key: str,
    base_url: str,
    timeout: float,
    label: str = "deepseek",
) -> Tuple[int, Dict[str, Any]]:
    """Один вызов ``/chat/completions``. Сетевой сбой → ``(0, {"error": ...})``.

    HTTP-код возвращается сырым, чтобы вызывающий мог различить 401 (ключ) и
    429 (квота) — для рутины это разные решения: первое чинит человек, второе
    проходит само.

    ``label`` попадает в строку учёта префикс-кэша: потребителей несколько, и
    доля кэша у массового конвейера и у разовой кнопки в UI — разные величины,
    которые нельзя складывать в одно среднее.
    """
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Bearer {api_key}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            parsed = json.loads(raw) if raw.strip() else {}
            if isinstance(parsed, dict):
                # Учёт не должен ронять уже оплаченный вызов: тело ответа
                # разобрано, деньги потрачены, вердикт вызывающему нужен даже
                # если метрика почему-то не сложилась.
                try:
                    log_cache_usage(label, parsed)
                except Exception:  # pragma: no cover — учёт не важнее ответа
                    logger.debug("deepseek-usage: не удалось записать метрику", exc_info=True)
            return int(resp.status), parsed
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # pragma: no cover — тело ошибки не обязано читаться
            pass
        return int(e.code), {"error": detail}
    except Exception as e:
        # Текст исключения может содержать URL; ключ у нас в заголовке, не в
        # пути (в отличие от Telegram Bot API — инцидент 2026-08-12), но обрезаем
        # всё равно: маскирование логов ловит остальное.
        return 0, {"error": f"{type(e).__name__}: {e}"[:300]}


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
    label: str = "deepseek",
) -> Dict[str, Any]:
    """Один диалоговый вызов. Возвращает ``{ok, content, model, usage}`` либо
    ``{ok: False, reason, detail?}``.

    Коды отказа: ``no_api_key`` | ``empty_prompt`` | ``network`` |
    ``http_<код>`` | ``empty_response``. Они попадают в логи и в ответы UI,
    поэтому стабильны — по ним же различаются «ключа нет» (чинит владелец) и
    «модель промолчала» (повторить).

    ``json_object=True`` включает у DeepSeek режим строгого JSON. Он не
    отменяет разбор на стороне вызывающего: режим гарантирует синтаксис, а не
    схему.
    """
    key = api_key if api_key is not None else get_api_key()
    if not key:
        return {"ok": False, "reason": "no_api_key"}
    if not (user or "").strip():
        return {"ok": False, "reason": "empty_prompt"}

    messages: List[Dict[str, str]] = []
    if system and system.strip():
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})

    body: Dict[str, Any] = {
        "model": model or get_model(),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens if max_tokens is not None else get_max_tokens(),
    }
    if json_object:
        body["response_format"] = {"type": "json_object"}

    status, response = call_api(
        body,
        api_key=key,
        base_url=get_base_url(),
        timeout=timeout if timeout is not None else get_timeout(),
        label=label,
    )
    if status == 0:
        return {"ok": False, "reason": "network", "detail": response.get("error")}
    if not (200 <= status < 300):
        return {"ok": False, "reason": f"http_{status}", "detail": response.get("error")}

    choices = response.get("choices") or []
    content = ""
    if choices and isinstance(choices[0], dict):
        content = str((choices[0].get("message") or {}).get("content") or "").strip()
    if not content:
        return {"ok": False, "reason": "empty_response"}

    usage = response.get("usage") if isinstance(response.get("usage"), dict) else None
    return {"ok": True, "content": content, "model": body["model"], "usage": usage}
