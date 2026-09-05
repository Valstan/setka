"""Прирост подписчиков сети — арифметика для публичного лендинга.

Заказ владельца 2026-08-27: на ``/regions/links`` рядом с «35 сообществ /
30 614 подписчиков» показать, **на сколько сеть выросла** — за сутки, за
30 дней, за полгода — и отдельной полосой помесячный прирост за последние три
месяца.

Источник — те же дневные снимки ``region_member_snapshots``, что кормят число
подписчиков в строке списка (ночная таска ``collect_member_snapshots``, 04:00
MSK). Здесь только чистые функции: на вход строки ``(region_id, snapshot_date,
members_count)``, на выход готовая к показу структура. БД и FastAPI — в
``web/api/regions.py``.

Три свойства данных, вокруг которых написан весь модуль (замерены разведкой
2026-08-27, подробности — в ``docs/PENDING_FOLLOWUPS.md``):

1. **История мелкая и с рваным краем.** Первый снимок на проде —
   **2026-06-07** (замерено после выката 27.08; разведка по коду ожидала 06-08,
   но таска отработала в день выката миграции 033). «Полгода» поэтому честно
   вырождается в «за всё время (с 7 июня)» и станет настоящим полугодом само,
   когда история дорастёт: окно, которое не помещается в данные, помечается
   ``partial`` и подписывается своей фактической датой.
2. **Дыры в днях молчаливые.** VK-ошибка внутри таски проглатывается, и день
   без единой строки выглядит в логе как успех. Поэтому «за сутки» считается
   между **фактически существующими** снимками (последний и предыдущий
   непустой день), а не «сегодня минус вчера»: наивная разность при дыре
   отдала бы либо ноль (ложь), либо двухдневный прирост под видом суточного
   (тоже ложь). Сколько дней реально прошло — в ``days`` каждого окна.
3. **Регионы включались волнами.** Район, заведённый в середине окна, вносит
   в прирост сети весь свой стартовый объём. Решение владельца 2026-08-27:
   показываем рост сети целиком, но рядом всегда едет, **сколько из него дали
   новые сообщества** (``new_communities`` / ``new_members``) — иначе
   подключение района читается как органический рост аудитории.

Сумма на дату считается «переносом вперёд» (carry-forward): у региона берётся
последний снимок **не позже** этой даты. Без переноса день неполного сбора
дал бы фантомный обвал и такой же фантомный отскок назавтра.
"""

from __future__ import annotations

from bisect import bisect_right
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

from utils.text_utils import plural_ru  # noqa: F401 — историческое имя, импортируют отсюда

# Окна верхней плашки: (ключ, сколько дней назад, заголовок).
# «Полгода» = 180 дней; когда история короче, заголовок заменяется на
# «за всё время (с <дата>)» — см. ``_window``.
WINDOW_SPECS: Tuple[Tuple[str, int, str], ...] = (
    ("day", 1, "за сутки"),
    ("month", 30, "за 30 дней"),
    ("half_year", 180, "за полгода"),
)

# Сколько месяцев показывает нижняя полоса (текущий + два предыдущих).
MONTHS_SHOWN = 3

_MONTHS_NOMINATIVE = (
    "январь",
    "февраль",
    "март",
    "апрель",
    "май",
    "июнь",
    "июль",
    "август",
    "сентябрь",
    "октябрь",
    "ноябрь",
    "декабрь",
)

_MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def format_day_ru(day: date) -> str:
    """«8 июня» — для подписи фактической границы окна."""
    return f"{day.day} {_MONTHS_GENITIVE[day.month - 1]}"


def format_count_ru(value: int) -> str:
    """«2 857» — неразрывный пробел между тысячами, как в плитках страницы.

    Клиент форматирует свои числа через ``toLocaleString('ru-RU')``; подписи
    собираются на сервере, и без этого «+4 490» в плитке соседствовало бы с
    «(+2857)» в подписи под ней.
    """
    return f"{int(value):,}".replace(",", " ")


def month_title_ru(year: int, month: int, current_year: int) -> str:
    """«июль», а для прошлого года — «декабрь 2025» (иначе полоса врёт на стыке)."""
    title = _MONTHS_NOMINATIVE[month - 1]
    return title if year == current_year else f"{title} {year}"


def index_snapshots(
    rows: Iterable[Tuple[Any, Any, Any]],
    region_ids: Optional[Iterable[int]] = None,
) -> Dict[int, Tuple[List[date], List[int]]]:
    """Строки снимков → ``{region_id: ([даты по возрастанию], [значения])}``.

    ``region_ids`` (если задан) сужает расчёт до **показываемого** состава сети:
    у деактивированного региона строки живут в таблице и без фильтра он
    продолжал бы входить в «было», превращая своё отключение в обвал сети.

    Дубли по (регион, день) невозможны — на них уникальный индекс
    ``uq_region_member_snapshot_day``; если такая строка всё же придёт,
    побеждает последняя (тот же порядок, что у upsert'а таски).
    """
    keep: Optional[Set[int]] = set(int(r) for r in region_ids) if region_ids is not None else None
    by_region: Dict[int, Dict[date, int]] = {}
    for region_id, snapshot_date, members_count in rows:
        if region_id is None or snapshot_date is None or members_count is None:
            continue
        rid = int(region_id)
        if keep is not None and rid not in keep:
            continue
        day = (
            snapshot_date
            if isinstance(snapshot_date, date)
            else date.fromisoformat(str(snapshot_date))
        )
        by_region.setdefault(rid, {})[day] = int(members_count)

    indexed: Dict[int, Tuple[List[date], List[int]]] = {}
    for rid, per_day in by_region.items():
        days = sorted(per_day)
        indexed[rid] = (days, [per_day[d] for d in days])
    return indexed


def total_as_of(
    indexed: Dict[int, Tuple[List[date], List[int]]], day: date
) -> Tuple[int, Set[int]]:
    """Подписчики сети на дату: сумма последних снимков **не позже** ``day``.

    Возвращает ``(сумма, множество регионов, попавших в сумму)``. Второй
    элемент — то, чем «новое сообщество» отличается от «выросшего»: регион,
    которого не было в составе на раннюю дату, но есть на позднюю, — новый.
    """
    total = 0
    counted: Set[int] = set()
    for rid, (days, values) in indexed.items():
        pos = bisect_right(days, day)
        if pos == 0:
            continue  # регион ещё не наблюдался на эту дату
        total += values[pos - 1]
        counted.add(rid)
    return total, counted


def _window(
    indexed: Dict[int, Tuple[List[date], List[int]]],
    *,
    key: str,
    title: str,
    from_day: date,
    to_day: date,
    first_day: date,
    partial: bool,
) -> Dict[str, Any]:
    """Одно окно прироста: дельта, состав, честная подпись границ."""
    then_total, then_ids = total_as_of(indexed, from_day)
    now_total, now_ids = total_as_of(indexed, to_day)
    fresh_ids = now_ids - then_ids
    new_members = 0
    for rid in fresh_ids:
        days, values = indexed[rid]
        pos = bisect_right(days, to_day)
        if pos:
            new_members += values[pos - 1]
    days_span = (to_day - from_day).days
    return {
        "key": key,
        "title": title,
        "delta": now_total - then_total,
        "from_date": from_day.isoformat(),
        "to_date": to_day.isoformat(),
        "days": days_span,
        "partial": partial,
        "new_communities": len(fresh_ids),
        "new_members": new_members,
        "note": _window_note(days_span, key, first_day, partial, len(fresh_ids), new_members),
    }


def _window_note(
    days_span: int,
    key: str,
    first_day: date,
    partial: bool,
    new_communities: int,
    new_members: int,
) -> str:
    """Подпись под числом: чем прирост на самом деле является.

    Две вещи, о которых молчать нечестно: окно шире заявленного (дыра в сборе
    растянула «сутки» на двое) и вклад только что подключённых сообществ.
    """
    parts: List[str] = []
    if key == "day" and days_span != 1:
        parts.append(f"фактически за {days_span} {plural_ru(days_span, 'день', 'дня', 'дней')}")
    if partial:
        parts.append(f"данные с {format_day_ru(first_day)}")
    if new_communities:
        noun = plural_ru(new_communities, "новое сообщество", "новых сообщества", "новых сообществ")
        parts.append(f"включая {new_communities} {noun} (+{format_count_ru(new_members)})")
    return " · ".join(parts)


def _month_bounds(anchor: date, months_back: int) -> Tuple[date, date]:
    """Границы месяца, отстоящего от ``anchor`` на ``months_back`` месяцев назад."""
    year, month = anchor.year, anchor.month - months_back
    while month <= 0:
        month += 12
        year -= 1
    return date(year, month, 1), date(year, month, monthrange(year, month)[1])


def build_growth(
    rows: Iterable[Tuple[Any, Any, Any]],
    *,
    region_ids: Optional[Iterable[int]] = None,
    today: Optional[date] = None,
) -> Optional[Dict[str, Any]]:
    """Полная статистика прироста для лендинга — или ``None``, если считать не из чего.

    ``None`` возвращается, когда снимков нет вовсе или он ровно один: одна
    точка — это «сколько сейчас», а не «на сколько выросли», и рисовать из неё
    плашку «+0» значило бы выдать отсутствие данных за отсутствие роста.

    ``today`` — календарное «сегодня» (по нему выбираются месяцы полосы), а
    не дата последнего снимка: полоса должна ехать по календарю, даже если
    сборщик встал. Расхождение видно по ``stale_days``.
    """
    indexed = index_snapshots(rows, region_ids)
    all_days = sorted({day for days, _ in indexed.values() for day in days})
    if len(all_days) < 2:
        return None

    first_day, latest_day = all_days[0], all_days[-1]
    today = today or date.today()

    windows: List[Dict[str, Any]] = []
    seen_bounds: Set[Tuple[str, str]] = set()
    for key, days_back, title in WINDOW_SPECS:
        if key == "day":
            # Предыдущий день, на который снимки ФАКТИЧЕСКИ есть, а не latest-1:
            # при дыре в сборе разность «сегодня минус вчера» либо занулится,
            # либо отдаст двухдневный прирост под видом суточного.
            from_day = all_days[-2]
            partial = False
        else:
            from_day = latest_day - timedelta(days=days_back)
            partial = from_day < first_day
            if partial:
                from_day = first_day
                title = f"за всё время (с {format_day_ru(first_day)})"
        if from_day >= latest_day:
            continue
        bounds = (from_day.isoformat(), latest_day.isoformat())
        if bounds in seen_bounds:
            # Короткая история схлопывает «30 дней» и «полгода» в одно и то же
            # окно — вторую такую же плашку не показываем.
            continue
        seen_bounds.add(bounds)
        windows.append(
            _window(
                indexed,
                key=key,
                title=title,
                from_day=from_day,
                to_day=latest_day,
                first_day=first_day,
                partial=partial,
            )
        )

    months: List[Dict[str, Any]] = []
    for back in range(MONTHS_SHOWN - 1, -1, -1):
        month_start, month_end = _month_bounds(today, back)
        to_day = min(month_end, latest_day)
        from_day = month_start - timedelta(days=1)
        entry: Dict[str, Any] = {
            "key": f"{month_start.year:04d}-{month_start.month:02d}",
            "title": month_title_ru(month_start.year, month_start.month, today.year),
            "current": back == 0,
            "delta": None,
            "partial": False,
            "new_communities": 0,
            "new_members": 0,
            "note": "",
        }
        if to_day >= first_day and to_day > from_day:
            partial = from_day < first_day
            if partial:
                from_day = first_day
            measured = _window(
                indexed,
                key="month",
                title=entry["title"],
                from_day=from_day,
                to_day=to_day,
                first_day=first_day,
                partial=partial,
            )
            entry.update(
                {
                    "delta": measured["delta"],
                    "partial": partial,
                    "new_communities": measured["new_communities"],
                    "new_members": measured["new_members"],
                    "from_date": measured["from_date"],
                    "to_date": measured["to_date"],
                }
            )
            notes: List[str] = []
            if partial:
                notes.append(f"с {format_day_ru(first_day)}")
            if entry["current"]:
                notes.append("месяц не закончился")
            if measured["new_communities"]:
                notes.append(f"+{measured['new_communities']} нов.")
            entry["note"] = " · ".join(notes)
        months.append(entry)

    total_now, counted_now = total_as_of(indexed, latest_day)
    return {
        "latest_date": latest_day.isoformat(),
        "first_date": first_day.isoformat(),
        "latest_date_human": format_day_ru(latest_day),
        "first_date_human": format_day_ru(first_day),
        # Сколько дней прошло с последнего снимка: 0-1 — норма (таска в 04:00),
        # больше — сборщик встал, и числа на странице надо читать «на дату».
        "stale_days": max(0, (today - latest_day).days),
        "total_members": total_now,
        "regions_counted": len(counted_now),
        "windows": windows,
        "months": months,
    }


def growth_query_start(today: date) -> date:
    """С какой даты тянуть снимки, чтобы хватило на все окна и полосу месяцев.

    Самая ранняя нужная граница — либо полугодовое окно, либо день перед
    началом самого раннего показываемого месяца; берём минимум и добавляем
    сутки запаса на дыру в сборе у самой границы.
    """
    half_year = today - timedelta(days=max(days for _, days, _ in WINDOW_SPECS))
    earliest_month_start, _ = _month_bounds(today, MONTHS_SHOWN - 1)
    return min(half_year, earliest_month_start) - timedelta(days=1)


def region_ids_from_blocks(
    blocks: Sequence[Dict[str, Any]], code_to_id: Dict[str, int]
) -> List[int]:
    """id показываемых регионов — по кодам из уже собранных блоков списка.

    Состав прироста обязан совпадать с составом видимого списка: считать по
    всем строкам таблицы значит включить в «было» регионы, которых на странице
    нет.
    """
    ids: List[int] = []
    for block in blocks:
        for item in block.get("items", []):
            rid = code_to_id.get(item.get("code"))
            if rid is not None:
                ids.append(rid)
    return ids
