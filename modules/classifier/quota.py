"""Квоты тем: потолок доли темы в ленте региона (заказ владельца 2026-08-30).

Владелец жаловался, что лентой не управляет: одних тем в ней слишком много,
других мало. Здесь — потолок: «не больше X% ленты за скользящее окно».

**Потолок, а не пол.** Квота умеет не пустить, но не умеет создать: если тема
даёт 4% кандидатов, ползунок в 20% её не поднимет. Механически потолок работает
и как слабый пол для недобранных тем — задавили доминирующую, освободившиеся
места добирают остальные, — но это следствие, а не гарантия. Настоящие рычаги
«поднять тему» лежат в другом месте (число beat-слотов и пул сообществ-источников
темы), и страница долей обязана показывать колонку «кандидатов, %», чтобы
недостижимая цель была видна глазами.

**Доли НЕ нормируются на свою сумму.** Каждая — независимый потолок, поэтому:
  * сумма ≠ 100 движок не ломает — это просто набор ограничений;
  * частичная настройка безопасна: задал одной теме 0, остальные без потолка;
  * а если бы нормировали, единственная заданная доля ``новости=50`` дала бы
    ``frac = 50/50 = 1.0``, и потолок исчез бы молча. Из всех ошибок, возможных
    здесь, эта самая тихая.

**Считаем от опубликованного, а не от кандидатов.** На вход отбора приходят
десятки постов, а в ленту выходят ``max_posts_per_bulletin`` (дефолт 3) плюс
хедлайнер. Списывать расход в момент отбора значит завышать его в разы, поэтому
остаток читается здесь, а списывается после публикации — журналом
``modules.publication_journal`` (тот же приём, что у суточного потолка раскрутки
в ``modules/promotion/dispatcher.py``).
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# Сколько мест в ленте добавляет текущая волна к знаменателю: сводка
# (max_posts_per_bulletin) плюс хедлайнер отдельным постом. Без этой добавки
# первая волна суток делила бы на ноль опубликованных и не пускала бы никого.
HEADLINER_SLOTS = 1


def theme_caps(
    shares: Mapping[str, Optional[float]],
    published: Mapping[str, int],
    *,
    slots: int,
) -> Dict[str, int]:
    """Сколько постов темы ЕЩЁ можно опубликовать в этой волне.

    ``total`` — знаменатель: всё опубликованное за окно плюс места этой волны.
    ``cap = ceil(share/100 * total)``; округление вверх, потому что при малых
    числах (первые часы окна) округление вниз давало бы ноль всем темам сразу и
    лента вставала бы целиком.

    Доля ``None`` в результат не попадает — «не ограничивать».
    """
    total = sum(int(v) for v in published.values()) + max(0, int(slots))
    caps: Dict[str, int] = {}
    for theme, share in shares.items():
        if share is None:
            continue
        try:
            share_value = float(share)
        except (TypeError, ValueError):
            continue
        if share_value <= 0:
            caps[theme] = 0
            continue
        cap = math.ceil(share_value / 100.0 * total)
        caps[theme] = max(0, cap - int(published.get(theme, 0)))
    return caps


def banned_themes(shares: Mapping[str, Optional[float]]) -> set:
    """Темы, которым публикация ЗАПРЕЩЕНА (доля 0), а не просто исчерпана.

    Разница принципиальная, хотя потолок в обоих случаях ноль. Исчерпанная квота
    — «на сегодня хватит», и если волна опустела целиком, из такой темы можно
    вернуть лучший пост. Запрет — «этого в ленте не будет», и правило непустой
    волны обязано его уважать, иначе владельцем убранная рубрика возвращается
    через чёрный ход ровно тогда, когда её никто не ждёт.
    """
    out = set()
    for theme, share in shares.items():
        if share is None:
            continue
        try:
            if float(share) <= 0:
                out.add(theme)
        except (TypeError, ValueError):
            continue
    return out


def apply_theme_quota(
    posts: Sequence[Dict[str, Any]],
    *,
    theme_of: Callable[[Dict[str, Any]], Optional[str]],
    rating_of: Callable[[Dict[str, Any]], Optional[float]],
    shares: Mapping[str, Optional[float]],
    published: Mapping[str, int],
    slots: int,
    min_posts: int = 1,
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Срезать посты сверх потолка темы. Возвращает ``(оставленные, убрано_по_темам)``.

    Порядок входа сохраняется: пересортировкой занимается сборщик сводки, а
    квота только вычитает — так её результат читается в логах рядом с отбором.

    Внутри темы остаются лучшие по ``rating_of`` — тем же ключом, что и
    ``BulletinBuilder._sort_by_popularity``: пост без измеренных просмотров
    (``None``) уходит в хвост, а не наверх.

    ``min_posts`` — правило непустой волны: если квота вычистила всё, а на входе
    было не пусто, вернуть лучшие ``min_posts`` среди тем, которым публикация не
    запрещена. Молчащая районная лента хуже небольшого перебора по доле.
    **Тема с долей 0 в это правило не входит никогда** — иначе запрет протёк бы
    через чёрный ход.
    """
    if not posts or not shares:
        return list(posts), {}

    caps = theme_caps(shares, published, slots=slots)
    if not caps:
        return list(posts), {}
    banned = banned_themes(shares)

    def _sort_key(item: Tuple[int, Dict[str, Any]]):
        score = rating_of(item[1])
        return (score is not None, score or 0.0)

    # Индексы постов, которые оставляем. Идём по темам, а не по общему списку:
    # потолок у каждой темы свой, и «лучшие внутри темы» — это именно внутри.
    keep: set = set()
    by_theme: Dict[str, List[Tuple[int, Dict[str, Any]]]] = {}
    for index, post in enumerate(posts):
        theme = theme_of(post)
        if theme is None or theme not in caps:
            # Тема неизвестна или без потолка — квота такой пост не трогает.
            keep.add(index)
            continue
        by_theme.setdefault(theme, []).append((index, post))

    for theme, items in by_theme.items():
        allowed = caps[theme]
        if allowed <= 0:
            continue
        ranked = sorted(items, key=_sort_key, reverse=True)
        for index, _ in ranked[:allowed]:
            keep.add(index)

    if not keep and min_posts > 0 and posts:
        # Волна опустела целиком — вернуть лучшее из разрешённого. Темы с долей 0
        # сюда не попадают: запрет не должен протекать через чёрный ход.
        candidates = [
            (index, post)
            for index, post in enumerate(posts)
            if (theme_of(post) or "") not in banned
        ]
        if candidates:
            ranked = sorted(candidates, key=_sort_key, reverse=True)[:min_posts]
            keep = {index for index, _ in ranked}
            logger.info("theme quota: волна опустела, возвращено %d лучших постов", len(keep))

    selected = [post for index, post in enumerate(posts) if index in keep]

    # Счётчик убранного считаем ПОСЛЕ всех решений, включая спасение, — иначе он
    # разойдётся с тем, что реально ушло в сводку.
    dropped: Dict[str, int] = {}
    for theme, items in by_theme.items():
        lost = sum(1 for index, _ in items if index not in keep)
        if lost:
            dropped[theme] = lost

    return selected, dropped
