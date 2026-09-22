"""Кэш стен ВК: одна стена читается один раз, а не каждой подсистемой заново.

**Зачем.** Заказ владельца 2026-09-22: «возможно, какие-то вещи делаются два
раза, три раза — сбор, парсинг». Разбор подтвердил: одни и те же стены читают
независимо друг от друга районные волны (по теме на каждую из ~26 тем в сутки),
каскад области, радар, copy/setka, Кругозор, зеркало Telegram, еженедельная
перепроверка сообществ и проверки уведомлений. Общего места, где лежит уже
прочитанное, не было вовсе.

Самый крупный и самый бессмысленный повтор — **собственная ИНФО-стена района**:
её сканирует КАЖДЫЙ прогон темы (сто постов), причём берётся из неё только
текст, ради вытаскивания ссылок на уже опубликованное (дедуп). На 44 районах
это около 1100 одинаковых вызовов в сутки.

**Чего этот модуль НЕ делает.** Он не превращает сеть в «единое хранилище
постов»: посты по-прежнему не складываются в общую таблицу, и подсистемы
по-прежнему ходят каждая за своим. Это дешёвый слой поверх клиента ВК, снимающий
повторное чтение ОДНОЙ И ТОЙ ЖЕ стены в пределах короткого окна.

**Два разных кэша, и разница между ними не в TTL, а в том, ЧТО хранится.**
Замер того же вечера развёл их окончательно: один работает, второй выключен.

* **Донорские стены — снимок стены целиком**, ``WALL_CACHE_TTL_SECONDS``
  (дефолт **0**, то есть выключено). Замер на проде: доля попаданий 2.3 % при
  росте пика памяти Redis с 5.3 до 37.9 МБ. Районы идут по очереди в одном
  процессе, доноры у каждого свои, повторного чтения одной стены почти не
  случается — ловить нечего при любом окне. Подробности у функции TTL.
* **Своя ИНФО-стена района — РЕЗУЛЬТАТ, а не сырьё**, ``WALL_HISTORY_CACHE_TTL_SECONDS``
  (дефолт 1800). Из ста постов своей стены нужен один список коротких строк —
  lip'ы источников, упомянутых в наших же сводках. Снимок стены весит около
  мегабайта, список — несколько килобайт.

  Первая редакция кэшировала здесь стену целиком, упиралась в потолок записи и
  тихо не кэшировала НИЧЕГО: в Redis не оказалось ни одного ключа своих стен, а
  в логе — ни одной жалобы (отказ по потолку писался в debug при прод-уровне
  INFO). Нашлось замером, не чтением кода. Отсюда два следствия в этом модуле:
  отказ по потолку теперь INFO, а история хранится результатом.

  Кэш истории **сбрасывается явно** сразу после публикации в эту стену
  (``invalidate_wall`` стирает обе записи), иначе следующая тема не увидела бы
  только что вышедшую сводку и могла бы выпустить её содержимое повторно.

**Ноль отключает кэш полностью** — откат без деплоя, одной переменной окружения.

**Fail-open во всём.** Redis недоступен, ключ битый, json не разобрался — это
промах кэша, а не ошибка: подсистема идёт в ВК, как ходила раньше. Кэш обязан
уметь исчезнуть бесследно.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

KEY_PREFIX = "setka:wall_cache"
HISTORY_KEY_PREFIX = "setka:wall_history_lips"

_redis_client = None
_redis_pid: Optional[int] = None
_warned_once = False


def _getenv(name: str, default: str) -> str:
    return (os.getenv(name) or default).strip()


def wall_cache_ttl_seconds() -> int:
    """TTL донорских стен. **Дефолт 0 — эта половина кэша выключена.**

    Не «на всякий случай», а по замеру на проде 2026-09-22, в тот же вечер,
    когда кэш выкатили:

    * **доля попаданий 2.3 %** — 11 попаданий против 474 промахов за вечер;
    * **пик памяти Redis вырос с 5.3 до 37.9 МБ**, то есть в семь раз.

    Причина структурная, а не настроечная. Районы обрабатываются по очереди
    одним дочерним процессом (ядро на боксе одно), донорские сообщества у
    каждого района свои, и одна и та же стена почти никогда не читается дважды
    в пределах окна. Ловить кэшу нечего — при любом TTL: увеличение окна
    поднимает не попадания, а резидентную память.

    Тридцать восемь мегабайт пика ради двух процентов вызовов — плохой обмен
    везде, а на боксе, где свободно 274 МБ и ядро убивает воркер по памяти
    (P164), он прямо противоречит остальной работе той же сессии.

    **Вторая половина кэша осталась и работает** — история своей стены
    (``get_cached_history_lips``): 53 района из 57, около 2 КБ на запись вместо
    мегабайта, и она снимает ~1100 повторных чтений в сутки. У неё свой TTL.

    Включить обратно — одна переменная окружения, если появится сценарий, где
    одну стену читают часто: например, много подсистем на один пул источников.
    """
    try:
        return max(0, int(float(_getenv("WALL_CACHE_TTL_SECONDS", "0"))))
    except ValueError:
        return 0


def wall_history_ttl_seconds() -> int:
    """TTL собственной стены района (история публикаций, только текст).

    Полчаса, а не час, и причина — память БОКСА, а не Redis.

    Кэш платит оперативной памятью за вызовы ВК, а платить ей здесь особенно
    нечем: на боксе 1536 МБ, свободно около 274 МБ, у Redis **не выставлен
    maxmemory** и политика ``noeviction`` — то есть он просто растёт. Ровно на
    этом боксе ядро убивает celery по памяти (P164), и сделать кэш новой
    причиной тех же убийств было бы обидно.

    Полчаса ловят главное — волны, стоящие в расписании кучно (на :20 три темы,
    на :30 три темы): своя стена читается один раз на такую кучу вместо трёх.
    Час дал бы сверх этого немного, а резидентную часть кэша увеличил бы вдвое.

    Поднять — одна переменная окружения, после того как замер на проде покажет
    реальный размер (``redis-cli info memory`` до и после).
    """
    try:
        return max(0, int(float(_getenv("WALL_HISTORY_CACHE_TTL_SECONDS", "1800"))))
    except ValueError:
        return 1800


def wall_cache_max_entry_bytes() -> int:
    """Потолок одной записи. Стена жирнее — не кэшируется вовсе.

    Страховка от патологии: пост с полусотней вложений или стена, где ВК отдал
    неожиданно много, не должны в одиночку занять десяток мегабайт на боксе,
    где их 274. Промах дешевле разросшегося Redis.
    """
    try:
        return max(0, int(float(_getenv("WALL_CACHE_MAX_ENTRY_BYTES", "262144"))))
    except ValueError:
        return 262144


def _redis():
    """Лениво-кэшированный, fork-safe Redis-клиент (db=1, decode_responses).

    PID-guard как у ``bulletin_heartbeat._redis``: пул соединений redis-py не
    переживает fork, а Celery-воркер форкается на каждой переработке ребёнка.

    Предупреждение печатается ОДИН раз на процесс: кэш зовётся на каждой стене,
    и строка на каждый промах утопила бы лог ровно тогда, когда Redis лежит.
    """
    global _redis_client, _redis_pid, _warned_once
    pid = os.getpid()
    if _redis_client is None or _redis_pid != pid:
        try:
            from modules.notifications.storage import NotificationsStorage

            _redis_client = NotificationsStorage().redis_client
            _redis_pid = pid
            _warned_once = False
        except Exception:  # pragma: no cover - инфраструктурный сбой
            if not _warned_once:
                logger.warning("wall cache: redis недоступен, работаем без кэша", exc_info=True)
                _warned_once = True
            _redis_client = None
            return None
    return _redis_client


def _key(owner_id: int) -> str:
    return f"{KEY_PREFIX}:{int(owner_id)}"


class WallCacheStats:
    """Счётчики одного клиента ВК — чтобы волна могла сказать, сработал ли кэш."""

    __slots__ = ("hits", "misses", "bypass")

    def __init__(self) -> None:
        self.hits = 0
        self.misses = 0
        self.bypass = 0

    def summary(self) -> str:
        return f"wall_cache hits={self.hits} misses={self.misses} bypass={self.bypass}"


def get_cached_wall(owner_id: int, count: int) -> Optional[List[Dict[str, Any]]]:
    """Посты стены из кэша, либо ``None`` (промах).

    Запись, сделанная под больший ``count``, обслуживает и меньший запрос:
    сотня постов собственной стены закрывает и чтение двадцати каскадом, и
    двадцати радаром. Обратное неверно — под меньший ``count`` в кэше просто
    нет нужных постов, это промах.
    """
    if wall_cache_ttl_seconds() <= 0 and wall_history_ttl_seconds() <= 0:
        return None
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(_key(owner_id))
        if not raw:
            return None
        payload = json.loads(raw)
        stored_count = int(payload.get("count") or 0)
        items = payload.get("items")
        if not isinstance(items, list) or stored_count < int(count):
            return None
        return items[: int(count)]
    except Exception:
        logger.debug("wall cache: чтение не удалось", exc_info=True)
        return None


def store_wall(
    owner_id: int,
    count: int,
    posts: List[Dict[str, Any]],
    *,
    ttl: Optional[int] = None,
) -> None:
    """Положить стену в кэш. Пустой ответ НЕ кэшируется.

    Пустая стена — это обычно временный отказ ВК (группа закрыта, токен в
    бане, ошибка сети), и запомнить его на пять минут значило бы превратить
    одну неудачу в серию: следующие подсистемы получили бы «постов нет» уже из
    кэша, не сходив в ВК.
    """
    effective_ttl = wall_cache_ttl_seconds() if ttl is None else int(ttl)
    if effective_ttl <= 0 or not posts:
        return
    client = _redis()
    if client is None:
        return
    try:
        payload = json.dumps(
            {"count": int(count), "items": posts},
            ensure_ascii=False,
            default=str,
        )
        cap = wall_cache_max_entry_bytes()
        size = len(payload.encode("utf-8"))
        if cap and size > cap:
            # INFO, а не debug. Отказ по потолку означает, что кэш для этой
            # стены НЕ работает — молча и без единого следа при прод-уровне
            # логов INFO. Ровно так первая редакция этого модуля выключила сама
            # себя на самом ценном случае, и заметил это только замер в Redis.
            logger.info(
                "wall cache: стена %s не кэширована — %d Б при потолке %d Б",
                owner_id,
                size,
                cap,
            )
            return
        client.setex(_key(owner_id), effective_ttl, payload)
    except Exception:
        logger.debug("wall cache: запись не удалась", exc_info=True)


def _history_key(owner_id: int) -> str:
    return f"{HISTORY_KEY_PREFIX}:{int(owner_id)}"


def get_cached_history_lips(owner_id: int) -> Optional[List[str]]:
    """lip'ы источников, вытащенные из НАШИХ сводок на своей стене. ``None`` — промах.

    **Кэшируется результат, а не сырьё, и это не микро-оптимизация.** Сотня
    постов своей стены весит около мегабайта, а нужен из них один список
    коротких строк — несколько килобайт. Первая редакция кэшировала стену
    целиком, упиралась в потолок записи и тихо не кэшировала НИЧЕГО: замер в
    Redis показал ноль ключей своих стен при нулевом числе жалоб в логе.
    """
    if wall_history_ttl_seconds() <= 0:
        return None
    client = _redis()
    if client is None:
        return None
    try:
        raw = client.get(_history_key(owner_id))
        if not raw:
            return None
        lips = json.loads(raw)
        return [str(x) for x in lips] if isinstance(lips, list) else None
    except Exception:
        logger.debug("wall cache: чтение истории не удалось", exc_info=True)
        return None


def store_history_lips(owner_id: int, lips) -> None:
    """Запомнить список lip'ов источников своей стены.

    Пустой список кэшируется намеренно, в отличие от пустой стены: «в наших
    сводках нет ссылок на источники» — это осмысленный факт о районе (например,
    он ещё ничего не публиковал), а не признак отказа ВК.
    """
    ttl = wall_history_ttl_seconds()
    if ttl <= 0:
        return
    client = _redis()
    if client is None:
        return
    try:
        client.setex(
            _history_key(owner_id),
            ttl,
            json.dumps(sorted(str(x) for x in lips), ensure_ascii=False),
        )
    except Exception:
        logger.debug("wall cache: запись истории не удалась", exc_info=True)


def invalidate_wall(owner_id: int) -> None:
    """Забыть стену. Зовётся после публикации в неё.

    Без этого следующая тема района читала бы историю публикаций из кэша и не
    видела только что вышедшую сводку — то есть могла бы выпустить её содержимое
    второй раз. Дедуп по ``work_tables.lip`` остаётся основным курсором и такой
    повтор поймал бы, но полагаться на второй рубеж там, где первый чинится
    одной строкой, неправильно.
    """
    client = _redis()
    if client is None:
        return
    try:
        # Обе записи разом: и снимок стены, и вытащенные из неё lip'ы. Забыть
        # одну и оставить вторую значило бы получить ровно ту же несвежесть,
        # ради которой сброс и делается.
        client.delete(_key(owner_id))
        client.delete(_history_key(owner_id))
    except Exception:
        logger.debug("wall cache: сброс не удался", exc_info=True)
