"""Реакция раскрутки на коды ошибок VK.

Проект уже умеет обращаться с кодами «токен мёртв» (5/17/29 — авто-отключение в
``modules/vk_token_router``), «аккаунт забанен» (10 — ротация каскада, G151) и
«метод недоступен групповому токену» (15/27 — фолбэк на user-токен). Не покрыты
ровно те коды, которыми VK говорит «ты ведёшь себя как спамер»:

    _VK_EXPECTED_ERROR_CODES = frozenset({15, 18, 203, 212, 220})   # vk_client.py

9, 14, 214 и 219 не встречаются в фильтрах нигде — попадут в общую ветку ERROR и
не изменят поведения. Для обычного парсинга это терпимо, для модуля, который сам
публикует по расписанию, — нет: именно эти коды и есть ранний сигнал бана.

**Разделение по смыслу.** 9 и 14 говорят о нас («поток слишком плотный», «докажи,
что не робот») — значит останавливается весь модуль. 214, 219, 220 говорят о
конкретной стене («сюда нельзя», «рекламный пост уже был») — значит в чёрный
список уходит один донор, а модуль работает дальше. Смешать их — либо встать из-за
одной неудачной группы, либо продолжать долбиться, когда VK уже сказал «хватит».

219 стоит особняком: это не «подожди», а «я прочитал твой пост как рекламу».
Повторять его через час бессмысленно и вредно, поэтому донор отдыхает неделю, а
владелец получает алёрт — текст шаблона надо переписывать, а не ретраить.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# VK-код в начале текста исключения: publisher поднимает наружу
# ``Exception("VK API error: [219] ...")`` и целочисленный код при этом теряется.
_CODE_RE = re.compile(r"\[(\d{1,4})\]")


@dataclass(frozen=True)
class PromoErrorAction:
    """Что делать с ошибкой.

    Attributes:
        kind: ``ok`` | ``retry`` | ``blacklist_donor`` | ``stop_module``.
        module_cooldown_seconds: на сколько замолкает весь модуль (0 — не молчит).
        blacklist_hours: на сколько донор уходит в чёрный список (0 — не уходит).
        alert: слать ли Telegram-алёрт владельцу.
        reason: человекочитаемая причина — уезжает в журнал и в чёрный список.
    """

    kind: str
    module_cooldown_seconds: int = 0
    blacklist_hours: int = 0
    alert: bool = False
    reason: str = ""


_DAY = 24 * 3600
_SIX_HOURS = 6 * 3600

_ACTIONS = {
    9: PromoErrorAction(
        kind="stop_module",
        module_cooldown_seconds=_DAY,
        alert=True,
        reason="VK 9: flood control — VK считает наш поток спамом",
    ),
    14: PromoErrorAction(
        kind="stop_module",
        module_cooldown_seconds=_SIX_HOURS,
        alert=True,
        reason="VK 14: требуется капча",
    ),
    214: PromoErrorAction(
        kind="blacklist_donor",
        blacklist_hours=24,
        reason="VK 214: публикация на этой стене запрещена",
    ),
    219: PromoErrorAction(
        kind="blacklist_donor",
        blacklist_hours=7 * 24,
        alert=True,
        reason="VK 219: пост прочитан как рекламный — переписать шаблон",
    ),
    220: PromoErrorAction(
        kind="blacklist_donor",
        blacklist_hours=24,
        reason="VK 220: у стены исчерпан лимит постов",
    ),
}

# Коды, которыми занимаются существующие слои проекта. Раскрутка их только
# записывает в журнал: ротацией каскада и cooldown токена ведает vk_token_router,
# и дублировать его решения здесь — верный способ развести две правды.
_HANDLED_ELSEWHERE = frozenset({5, 10, 15, 17, 27, 29})


def extract_vk_error_code(message: Optional[str]) -> Optional[int]:
    """Достать код VK из текста исключения. ``None`` — кода в тексте нет.

    >>> extract_vk_error_code("VK API error: [219] Advertisement post was recently added")
    219
    >>> extract_vk_error_code("Captcha needed") is None
    True
    """
    if not message:
        return None
    match = _CODE_RE.search(str(message))
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def classify_promo_error(code: Optional[int], message: Optional[str] = None) -> PromoErrorAction:
    """Решить, что делать с ошибкой публикации.

    ``code`` можно не передавать — тогда он вынимается из ``message``. Капча
    прилетает и текстом «Captcha needed» без кода (так её видит копи-сетка), и
    этот случай приравнивается к коду 14: иначе самый частый анти-бот сигнал VK
    остался бы неопознанным просто из-за формы записи.
    """
    if code is None:
        code = extract_vk_error_code(message)

    if code is None and message and "captcha" in str(message).lower():
        return _ACTIONS[14]

    if code is None:
        return PromoErrorAction(kind="retry", reason="ошибка без кода VK")

    action = _ACTIONS.get(code)
    if action is not None:
        return action

    if code in _HANDLED_ELSEWHERE:
        return PromoErrorAction(kind="retry", reason=f"VK {code}: обрабатывается каскадом токенов")

    return PromoErrorAction(kind="retry", reason=f"VK {code}: неизвестный код")


def is_stop_signal(action: PromoErrorAction) -> bool:
    """Требует ли ошибка остановки всего модуля."""
    return action.kind == "stop_module"
