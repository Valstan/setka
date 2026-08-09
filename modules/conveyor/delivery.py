"""Доставка поста на ingest-эндпойнт сайта — выход конвейера (D-015).

Контракт приёмника (директива brain 2026-07-26, источник истины — шапка
``vMalmyzhe/web/src/app/api/ingest/posts/route.ts``):

  ``POST <ingest_url>``, заголовок ``X-Gateway-Key``, тело —
  ``{vkPostId, sourceUrl, title?, text?, section?, date?, images?}``.
  Приёмник **всегда создаёт черновик**, публикует человек; повторная доставка
  того же ``vkPostId`` обновляет черновик и не трогает опубликованное.

Отсюда два следствия, которые здесь и реализованы:

* **Ретраить 502/503/504 можно ровно потому, что приёмник идемпотентен по
  ``vkPostId``** (G234 — гейтвейные пятисотки на этом маршруте будут). Появится
  доставка без ключа идемпотентности — ретрай начнёт задваивать новости, и
  тогда этот код придётся менять, а не копировать.
* **Безлюдная исходящая ветка обязана иметь дешёвый инвариант на мусор**
  (pool #133 — наш же урок 08.08, когда авто-приветствие полтора дня уходило
  нечитаемым). Порог ``?``×3 из того инцидента сюда НЕ переносится: он про
  потерянную кодировку. Здесь предикаты свои — см. ``check_invariant``.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Лимиты приёмника из контракта: 10 картинок, 15 МБ суммарно. Размер мы не
# знаем (файлы качает приёмник), поэтому держим только счётный лимит — лишние
# ссылки просто не отправляем, чтобы не получить частичный отказ.
MAX_IMAGES = 10

# Коридор длины текста. Нижняя граница — пост, из которого новости не выйдет
# (подпись под фото, «см. в источнике»); верхняя — предохранитель от аномалии
# вроде склеенной простыни, а не редакционное правило.
MIN_TEXT_CHARS = 80
MAX_TEXT_CHARS = 20000

# Нераскрытый плейсхолдер: {author_name}, {{name}}, %s, $VAR в тексте новости —
# признак того, что шаблон уехал на публику неподставленным.
_PLACEHOLDER_RE = re.compile(
    r"\{\{?\s*[a-zA-Z_][\w\s]*\}?\}|%\((?:\w+)\)s|(?<!\w)\$\{?[A-Z_]{3,}\}?"
)

# Коды, на которых ретрай осмыслен: гейтвей ещё не проснулся / перезапускается.
RETRIABLE_STATUS = (502, 503, 504)


def lip_to_vk_post_id(lip: str) -> Optional[str]:
    """``158787639_77646`` → ``-158787639_77646`` — ключ идемпотентности приёмника.

    ``lip`` хранит owner по модулю (так его пишет сбор), а приёмник ждёт
    ВК-нотацию со знаком: у сообществ owner отрицательный. Без знака идемпотентность
    приёмника не сработает — он заведёт вторую запись на тот же пост.
    """
    raw = (lip or "").strip()
    if not raw or "_" not in raw:
        return None
    owner, _, post = raw.partition("_")
    if not owner.isdigit() or not post.isdigit():
        return None
    return f"-{owner}_{post}"


def _letters_ratio(text: str) -> float:
    """Доля букв среди непробельных символов. Пусто → 0.0."""
    body = [c for c in text if not c.isspace()]
    if not body:
        return 0.0
    return sum(1 for c in body if c.isalpha()) / len(body)


def check_invariant(payload: Dict[str, Any]) -> Optional[str]:
    """Дешёвый инвариант на мусор. ``None`` — можно слать, иначе код причины.

    Проверяем **то, что уходит наружу**, а не исходный пост: подстановки и
    редактура происходят до этой точки, и здоровый на вид исходник ничего не
    гарантирует про результат (урок G235 — там проверять надо было шаблон, а не
    отрендеренный текст; здесь ровно наоборот, потому что рендер уже позади).
    """
    text = str(payload.get("text") or "")
    title = str(payload.get("title") or "").strip()

    if not str(payload.get("vkPostId") or "").strip():
        return "no_vk_post_id"
    if not str(payload.get("sourceUrl") or "").strip():
        return "no_source_url"
    if not title:
        return "empty_title"
    if _PLACEHOLDER_RE.search(text) or _PLACEHOLDER_RE.search(title):
        return "unresolved_placeholder"
    stripped = text.strip()
    if len(stripped) < MIN_TEXT_CHARS:
        return "text_too_short"
    if len(stripped) > MAX_TEXT_CHARS:
        return "text_too_long"
    if _letters_ratio(stripped) < 0.5:
        return "low_letter_ratio"
    return None


def build_payload(
    post: Dict[str, Any],
    verdict: Dict[str, Any],
    *,
    date_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Собрать тело запроса из поста (источник) и вердикта LLM (рубрика, заголовок, текст).

    Текст берём отредактированный, если он есть, иначе исходный: правило «фактов
    не добавлять» действует в промпте, а здесь мы просто не теряем пост, если
    редактура почему-то не вернулась.
    """
    images: List[str] = []
    for m in post.get("media") or []:
        if not isinstance(m, dict):
            continue
        url = str(m.get("url") or "").strip()
        # Только то, что приёмник умеет переложить: у видео/аудио/ссылок в
        # сводке медиа URL нет вовсе, и слать пустышку незачем.
        if url and m.get("type") in ("photo", "doc"):
            images.append(url)
        if len(images) >= MAX_IMAGES:
            break

    body: Dict[str, Any] = {
        "vkPostId": lip_to_vk_post_id(str(post.get("lip") or "")) or "",
        "sourceUrl": str(post.get("url") or "").strip(),
        "title": str(verdict.get("title") or "").strip(),
        "text": str(verdict.get("text") or post.get("text") or "").strip(),
    }
    section = str(verdict.get("section") or "").strip()
    if section:
        body["section"] = section
    if date_iso:
        body["date"] = date_iso
    if images:
        body["images"] = images
    return body


def idna_url(url: str) -> str:
    """Привести хост к punycode. Не-ASCII домен иначе роняет запрос ещё до сети.

    ``urllib`` кодирует заголовки запроса в latin-1, и кириллический хост даёт
    ``UnicodeEncodeError`` — не таймаут, не отказ сервера, а исключение внутри
    клиента. В журнале это выглядело как ``network`` ×18: связи нет, хотя сервер
    жив и на punycode-адрес отвечает.

    Кодируем здесь, а не в конфиге: в ``SITES`` домен остаётся кириллицей, каким
    его читает человек, и любой следующий сайт кластера заработает без правки.
    Путь и параметры не трогаем — punycode относится только к имени хоста.
    """
    raw = (url or "").strip()
    if not raw or raw.isascii():
        return raw
    try:
        parts = urllib.parse.urlsplit(raw)
        host = parts.hostname or ""
        if not host or host.isascii():
            return raw
        encoded = host.encode("idna").decode("ascii")
        netloc = encoded
        if parts.port:
            netloc = f"{netloc}:{parts.port}"
        if parts.username:
            cred = parts.username
            if parts.password:
                cred = f"{cred}:{parts.password}"
            netloc = f"{cred}@{netloc}"
        return urllib.parse.urlunsplit(
            (parts.scheme, netloc, parts.path, parts.query, parts.fragment)
        )
    except Exception:
        # Кодек idna спотыкается на экзотике (слишком длинная метка и т.п.).
        # Возвращаем как было: пусть падает на вызове с внятной ошибкой, а не
        # тихо подменяется на что-то другое.
        return raw


def _post_once(
    url: str,
    key: str,
    body: Dict[str, Any],
    *,
    timeout: float,
) -> Tuple[int, Dict[str, Any]]:
    """Один HTTP-вызов. Возвращает ``(http_status, ответ)``; сетевой сбой → ``(0, {...})``."""
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        idna_url(url),
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "X-Gateway-Key": key,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except ValueError:
                parsed = {"raw": raw[:500]}
            return int(resp.status), parsed if isinstance(parsed, dict) else {"raw": parsed}
    except urllib.error.HTTPError as e:
        raw = ""
        try:
            raw = e.read().decode("utf-8", "replace")
        except Exception:  # pragma: no cover — тело ошибки не обязано читаться
            pass
        return int(e.code), {"error": raw[:500]}
    except Exception as e:  # сеть/таймаут/DNS — не отличаем, все ретраибельны
        return 0, {"error": f"{type(e).__name__}: {e}"[:500]}


def deliver(
    site: Dict[str, Any],
    key: str,
    body: Dict[str, Any],
    *,
    timeout: float = 20.0,
    attempts: int = 3,
    sleep=None,
) -> Dict[str, Any]:
    """Доставить один пост. Возвращает ``{ok, status, attempts, remote_id?, reason?, response}``.

    Ретраим 502/503/504 и сетевые сбои — и только их. 4xx не ретраим: это наша
    ошибка в теле или ключе, повтор её не исправит, а лог засорит.

    ``sleep`` — инъекция паузы между попытками (в тестах пусто, в проде
    ``time.sleep``). Пауза линейная: конвейер не в горячем пути, изощряться с
    экспонентой незачем.
    """
    url = str(site.get("ingest_url") or "").strip()
    if not url:
        return {"ok": False, "status": 0, "attempts": 0, "reason": "no_ingest_url", "response": {}}
    if not key:
        return {"ok": False, "status": 0, "attempts": 0, "reason": "no_key", "response": {}}

    tries = max(1, attempts)
    status, response = 0, {}
    for i in range(1, tries + 1):
        status, response = _post_once(url, key, body, timeout=timeout)
        if 200 <= status < 300:
            return {
                "ok": True,
                "status": status,
                "attempts": i,
                "remote_id": str(response.get("id") or response.get("docId") or "") or None,
                "response": response,
            }
        if status not in RETRIABLE_STATUS and status != 0:
            return {
                "ok": False,
                "status": status,
                "attempts": i,
                "reason": f"http_{status}",
                "response": response,
            }
        if i < tries and sleep is not None:
            sleep(i * 2)
    return {
        "ok": False,
        "status": status,
        "attempts": tries,
        "reason": "network" if status == 0 else f"http_{status}",
        "response": response,
    }


def unknown_sections(site: Dict[str, Any], sections: Sequence[str]) -> List[str]:
    """Рубрики, которых нет в списке приёмника — обратная связь, а не блокировка.

    Приёмник неизвестный slug не считает отказом (создаёт пост без рубрики и
    возвращает warning), а директива прямо запрещает молча подгонять поток под
    его список. Поэтому расхождение мы копим и возвращаем людям, а не глушим.
    """
    known = {str(s).strip().lower() for s in (site.get("sections") or ())}
    if not known:
        return []
    seen: List[str] = []
    for s in sections:
        v = str(s or "").strip().lower()
        if v and v not in known and v not in seen:
            seen.append(v)
    return seen
