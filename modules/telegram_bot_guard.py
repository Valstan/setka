"""Сторож целостности Telegram-ботов: имя, описание, вебхук.

**Зачем.** Инцидент 2026-08-12: токены `@malm_info_bot` и `@valstan_bot`
утекли, и атакующий сделал ровно то, что даёт токен без всякого доступа к
аккаунту владельца:

- переименовал ботов (``setMyName``) в рекламу казино — и **каждый наш пост в
  канале стал подписан его рекламой**, хотя сам он не опубликовал ничего;
- переписал описание (``setMyDescription``) на ссылки с реферальным кодом;
- поставил свой **вебхук**, забрав все входящие апдейты (личка, посты и
  комментарии каналов, заявки на вступление) себе на сервер.

Заметили глазами, случайно, спустя дни. Ни один наш монитор не смотрел на то,
как бот выглядит снаружи: мы проверяли, что публикация **прошла**, и она
проходила — угон не ломает отправку, он ей пользуется.

**Что проверяем и почему именно это** (pool #133 — «у безлюдной исходящей
ветки обязан быть дешёвый инвариант»; pool #135 — «кто напишет сюда следующее
значение и что его остановит?»):

* ``username`` — не меняется никогда, это адрес бота;
* ``name`` — то, что видит подписчик под каждым постом;
* ``description`` / ``short_description`` — витрина в карточке бота;
* **вебхук обязан быть пустым** — мы работаем только через ``getUpdates``
  (``modules/radar/bot_intake.py``), вебхука у нас нет ни у одного бота, и
  любой непустой ``url`` означает, что апдейты уходят кому-то ещё.

**Эталон живёт в git, а не в БД и не в Redis.** Соблазн «снять эталон при
первом запуске и запомнить» надо погасить сразу: если первый запуск случится
после угона, эталоном станет имя атакующего, и сторож будет охранять взлом.
Значение эталона обязано появляться только через осознанную правку человеком —
поэтому оно здесь, под ревью и с историей.

Смена имени бота владельцем = правка ``EXPECTED_BOTS`` тем же PR. Это не
неудобство, а сам смысл инварианта: изменение витрины сети должно быть
чьим-то решением, а не тихим фактом.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_TIMEOUT = 15
# Не спамить, пока инцидент не разобран. Шесть часов — как у watchdog'а сводок.
ALERT_COOLDOWN_SECONDS = 6 * 3600
_COOLDOWN_KEY = "setka:telegram_bot_guard:alert_cooldown"


@dataclass(frozen=True)
class BotExpectation:
    """Как бот обязан выглядеть снаружи. Ключ — имя токена в ``TELEGRAM_TOKENS``."""

    username: str
    name: str
    description: str
    short_description: str


# Снято с живых ботов 2026-08-12 после ротации токенов и восстановления имён.
EXPECTED_BOTS: Dict[str, BotExpectation] = {
    "AFONYA": BotExpectation(
        username="malm_info_bot",
        name="Малмыж-Инфо",
        description="Малмыж-Инфо",
        short_description="Малмыж-Инфо",
    ),
    "VALSTANBOT": BotExpectation(
        username="valstan_bot",
        name="Жемчужинка",
        description="Жемчужинка",
        short_description="Жемчужинка",
    ),
}


@dataclass
class BotFinding:
    """Одно расхождение с эталоном."""

    bot: str
    field_name: str
    expected: str
    actual: str

    def as_line(self) -> str:
        return f"{self.bot}.{self.field_name}: ожидалось {self.expected!r}, сейчас {self.actual!r}"


@dataclass
class GuardResult:
    checked: List[str] = field(default_factory=list)
    skipped: Dict[str, str] = field(default_factory=dict)  # bot -> причина
    findings: List[BotFinding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.findings


def _call(token: str, method: str) -> dict:
    """Вызов Bot API. Сетевую ошибку и ответ API различаем — это разные исходы."""
    import requests

    resp = requests.get(_API.format(token=token, method=method), timeout=_TIMEOUT)
    try:
        payload = resp.json()
    except ValueError:
        return {"ok": False, "description": f"non-json response, HTTP {resp.status_code}"}
    return payload


def check_bot(bot: str, token: str, expected: BotExpectation) -> GuardResult:
    """Сверить одного бота с эталоном.

    Сетевой сбой → ``skipped`` (обрыв связи не инцидент безопасности, а шум).
    Ответ API с ``ok: false`` → **finding**: невалидный токен означает, что его
    отозвали, а мы об этом не знаем — это ровно то состояние, из-за которого
    бот молча перестаёт работать.
    """
    result = GuardResult()
    try:
        me = _call(token, "getMe")
    except Exception as e:  # noqa: BLE001 — сеть, не безопасность
        logger.warning("telegram bot guard: %s недоступен по сети: %s", bot, type(e).__name__)
        result.skipped[bot] = "network"
        return result

    if not me.get("ok"):
        result.findings.append(
            BotFinding(bot, "getMe", "ok", str(me.get("description") or "not ok"))
        )
        return result

    me_result = me.get("result") or {}
    result.checked.append(bot)

    actual_username = str(me_result.get("username") or "")
    if actual_username != expected.username:
        result.findings.append(BotFinding(bot, "username", expected.username, actual_username))

    actual_name = str(me_result.get("first_name") or "")
    if actual_name != expected.name:
        result.findings.append(BotFinding(bot, "name", expected.name, actual_name))

    text_fields = (
        ("getMyDescription", "description", expected.description),
        ("getMyShortDescription", "short_description", expected.short_description),
    )
    for method, key, want in text_fields:
        try:
            payload = _call(token, method)
        except Exception:  # noqa: BLE001
            continue  # частичный сбой не отменяет уже найденное
        if not payload.get("ok"):
            continue
        actual = str((payload.get("result") or {}).get(key) or "")
        if actual != want:
            result.findings.append(BotFinding(bot, key, want, actual))

    try:
        hook = _call(token, "getWebhookInfo")
    except Exception:  # noqa: BLE001
        return result
    if hook.get("ok"):
        url = str((hook.get("result") or {}).get("url") or "")
        if url:
            # Самый громкий из признаков: вебхука у нас нет ни у одного бота,
            # значит апдейты уходят на чужой приёмник прямо сейчас.
            result.findings.append(BotFinding(bot, "webhook", "", url))
    return result


def run_guard(
    tokens: Dict[str, str], expectations: Optional[Dict[str, BotExpectation]] = None
) -> GuardResult:
    """Сверить всех известных ботов. Токен без эталона — тоже finding.

    Неизвестный ``TELEGRAM_TOKEN_<NAME>`` означает, что бот появился в
    окружении мимо ревью: либо забыли дописать эталон, либо ключ положили в
    комнату КАРМАНа не мы. Оба случая надо увидеть, а не пропустить.
    """
    expectations = expectations if expectations is not None else EXPECTED_BOTS
    combined = GuardResult()
    # Одно и то же значение может лежать под двумя именами: back-compat
    # ``TELEGRAM_ALERT_BOT_TOKEN`` регистрируется как "ALERT" рядом с основным
    # именем бота (config/runtime.py). Псевдоним — не новый бот, эталон ему не
    # нужен; сверяется он всё равно, но под своим настоящим именем.
    known_values = {tokens.get(name) for name in expectations if tokens.get(name)}
    for bot, token in sorted(tokens.items()):
        expected = expectations.get(bot)
        if expected is None:
            if token and token in known_values:
                combined.skipped[bot] = "alias"
                continue
            combined.findings.append(BotFinding(bot, "expectation", "known bot", "нет эталона"))
            continue
        if not token:
            combined.skipped[bot] = "no-token"
            continue
        one = check_bot(bot, token, expected)
        combined.checked.extend(one.checked)
        combined.skipped.update(one.skipped)
        combined.findings.extend(one.findings)

    for bot in sorted(set(expectations) - set(tokens)):
        combined.skipped[bot] = "no-token"
    return combined


def format_alert(result: GuardResult) -> str:
    """Текст Telegram-алёрта. Без значений токенов — только имена и поля."""
    lines = [
        "🚨 <b>SETKA: бот изменён извне</b>\n",
        "Сверка с эталоном (<code>modules/telegram_bot_guard.py</code>) нашла расхождения:",
        "",
    ]
    for f in result.findings:
        actual = f.actual if len(f.actual) <= 120 else f.actual[:117] + "…"
        expected = f.expected or "(пусто)"
        lines.append(f"• <b>{f.bot}.{f.field_name}</b>")
        lines.append(f"  ожидалось: <code>{expected}</code>")
        lines.append(f"  сейчас: <code>{actual or '(пусто)'}</code>")
    lines += [
        "",
        "Если это <b>не</b> ваша правка — токен утёк. Порядок: "
        "<code>/revoke</code> в @BotFather → новое значение в комнату КАРМАНа → "
        "рестарт сервисов. Вебхук снимается вместе с отзывом токена.",
    ]
    return "\n".join(lines)


def maybe_alert(
    *,
    tokens: Dict[str, str],
    telegram_token: Optional[str] = None,
    chat_id: Optional[str] = None,
    redis_client: object = None,
    now: Optional[float] = None,
) -> str:
    """Прогнать сторожа и, при расхождениях, послать алёрт (с cooldown).

    Возвращает статус: ``ok`` | ``skipped:no-bots`` | ``skipped:no-telegram-config``
    | ``skipped:cooldown`` | ``alert-sent`` | ``error:…``.
    """
    if not tokens:
        return "skipped:no-bots"

    result = run_guard(tokens)
    if result.ok:
        logger.info(
            "telegram bot guard: ok (проверено %d, пропущено %d)",
            len(result.checked),
            len(result.skipped),
        )
        return "ok"

    for f in result.findings:
        logger.error("telegram bot guard: %s", f.as_line())

    if not telegram_token or not chat_id:
        return "skipped:no-telegram-config"

    current = now if now is not None else time.time()
    if redis_client is not None:
        try:
            if redis_client.get(_COOLDOWN_KEY):
                return "skipped:cooldown"
        except Exception:  # noqa: BLE001 — cooldown не критичен
            pass

    try:
        import requests

        resp = requests.post(
            _API.format(token=telegram_token, method="sendMessage"),
            json={
                "chat_id": chat_id,
                "text": format_alert(result),
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=_TIMEOUT,
        )
        if resp.status_code != 200:
            return f"error:http-{resp.status_code}"
    except Exception as e:  # noqa: BLE001
        return f"error:{type(e).__name__}"

    if redis_client is not None:
        try:
            redis_client.setex(_COOLDOWN_KEY, ALERT_COOLDOWN_SECONDS, int(current))
        except Exception:  # noqa: BLE001
            pass
    return "alert-sent"
