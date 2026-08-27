"""Подбор пары «донор → цель»: кто из сильных сообществ представляет слабый район.

Замер 28.08.2026 объясняет, почему здесь три эшелона, а не один.

    досягаемость сильного донора (≥1000 подписчиков) для 24 слабых районов
    хоп 1 (прямой сосед)     — 4 района
    хоп 2 (сосед соседа)     — ещё 9, итого 13
    недосягаемы вовсе        — 11 районов

Одним соседством закрывается шестая часть задачи: июльско-августовская экспансия
создала сплошной северный куст, где у всех соседей по два подписчика. Второй хоп
добирает половину, остальным остаётся областная группа — и она слабая (953
подписчика на всю область), о чём UI обязан говорить прямо.

**Донор со своим community-токеном идёт первым, и это не мелочь ранжирования.**
Такая публикация тратит ключ сообщества, а не user-аккаунт. User-токенов у проекта
два, ими же публикуются сводки всех 38 районов, и бан гасит продукт целиком
(GOTCHAS G151). Первый ключ сортировки переводит основной канал из «расходует
критичный аккаунт» в «не расходует вовсе».
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set

# Дальше второго хопа не ходим: «сосед соседа соседа» — уже не сосед ни в каком
# человеческом смысле, а пост «подпишитесь на район за 250 км» не конвертирует.
MAX_HOP = 2

HOP_NEIGHBOR = 1
HOP_SECOND = 2
HOP_OBLAST = 3


@dataclass(frozen=True)
class DonorCandidate:
    """Сильное сообщество, способное представить слабый район."""

    region_id: int
    code: str
    group_id: int
    members: int
    has_community_token: bool
    given_last_30d: int = 0


@dataclass(frozen=True)
class TargetCandidate:
    """Слабый район, которому нужна аудитория."""

    region_id: int
    code: str
    group_id: int
    members: Optional[int]
    received_total: int = 0


@dataclass(frozen=True)
class PromoPair:
    """Готовая пара на публикацию."""

    donor: DonorCandidate
    target: TargetCandidate
    hop: int

    @property
    def channel(self) -> str:
        return "promo_post"


@dataclass(frozen=True)
class OrphanTarget:
    """Район, которому сетевого донора не нашлось.

    Отдельный тип, а не молчаливый пропуск: UI обязан показать такие районы
    списком с честной подписью «сетевого донора нет, работают только находимость
    и ручной аутрич». Молчаливый пропуск читается как «район в работе».
    """

    target: TargetCandidate
    reason: str


def symmetrize(graph: Mapping[str, Sequence[str]]) -> Dict[str, Set[str]]:
    """Сделать граф соседства двусторонним.

    ``build_neighbor_graph`` возвращает то, что записано в ``regions.neighbors``,
    а это ручные CSV-списки: ребро может быть проставлено с одной стороны и
    забыто с другой. На текущих данных симметризация ничего не меняет (проверено
    28.08), но соседство физически двусторонне, и полагаться на аккуратность
    ручного заполнения незачем — тем более что миграция графа помечена как
    требующая проверки владельцем.
    """
    result: Dict[str, Set[str]] = {code: set() for code in graph}
    for code, neighbors in graph.items():
        for neighbor in neighbors:
            if not neighbor or neighbor == code:
                continue
            result.setdefault(code, set()).add(neighbor)
            result.setdefault(neighbor, set()).add(code)
    return result


def neighbors_at_hop(graph: Mapping[str, Set[str]], code: str, hop: int) -> Set[str]:
    """Коды, отстоящие от ``code`` ровно на ``hop`` рёбер."""
    if hop < 1:
        return set()
    seen: Set[str] = {code}
    frontier: Set[str] = {code}
    for _ in range(hop):
        nxt: Set[str] = set()
        for node in frontier:
            nxt |= graph.get(node, set())
        frontier = nxt - seen
        seen |= frontier
    return frontier


def _donor_sort_key(donor: DonorCandidate, hop: int):
    """Порядок предпочтения донора.

    Ключи по убыванию важности: свой community-токен (не тратим user-аккаунт) →
    ближе по графу → крупнее аудитория → меньше уже отдавал за месяц (не сажаем
    одну и ту же стену) → код, чтобы порядок был детерминированным и тесты не
    зависели от порядка строк из БД.
    """
    return (
        0 if donor.has_community_token else 1,
        hop,
        -donor.members,
        donor.given_last_30d,
        donor.code,
    )


def _target_sort_key(target: TargetCandidate):
    """Слабейшие и обделённые — первыми. ``None`` подписчиков считаем нулём."""
    return (
        target.members if target.members is not None else 0,
        target.received_total,
        target.code,
    )


def plan_pairs(
    targets: Iterable[TargetCandidate],
    donors: Iterable[DonorCandidate],
    graph: Mapping[str, Sequence[str]],
    *,
    second_hop_enabled: bool = True,
    max_pairs: int = 3,
    busy_donor_group_ids: Sequence[int] = (),
    busy_target_region_ids: Sequence[int] = (),
):
    """Подобрать пары на текущий слот.

    Жадный матчинг: один донор — одна цель, одна цель — один донор. Цели
    перебираются от слабейшей, для каждой берётся лучший свободный донор.

    Args:
        targets: слабые районы.
        donors: сильные сообщества (уже отфильтрованные по порогу и чёрному списку).
        graph: результат ``build_neighbor_graph`` — симметризуется здесь же.
        second_hop_enabled: разрешён ли поиск через соседа соседа.
        max_pairs: сколько пар вернуть максимум (суточный потолок действий).
        busy_donor_group_ids: доноры, уже отработавшие в этом слоте.
        busy_target_region_ids: цели, уже получившие промо в этом слоте.

    Returns:
        ``(pairs, orphans)`` — пары к публикации и районы без сетевого донора.
        Второй список никогда не выбрасываем: он и есть честный ответ на вопрос
        «почему этот район ничего не получает».
    """
    sym = symmetrize(graph)
    donors_by_code = {d.code: d for d in donors}
    busy_donors = set(busy_donor_group_ids)
    busy_targets = set(busy_target_region_ids)

    ordered_targets = sorted(targets, key=_target_sort_key)
    pairs: List[PromoPair] = []
    orphans: List[OrphanTarget] = []
    used_donor_groups: Set[int] = set(busy_donors)

    max_hop = MAX_HOP if second_hop_enabled else HOP_NEIGHBOR

    for target in ordered_targets:
        if target.region_id in busy_targets:
            continue

        best: Optional[PromoPair] = None
        reachable_any = False

        for hop in range(HOP_NEIGHBOR, max_hop + 1):
            candidates = []
            for code in neighbors_at_hop(sym, target.code, hop):
                donor = donors_by_code.get(code)
                if donor is None or donor.group_id == target.group_id:
                    continue
                reachable_any = True
                if donor.group_id in used_donor_groups:
                    continue
                candidates.append(donor)

            if not candidates:
                continue

            candidates.sort(key=lambda d: _donor_sort_key(d, hop))
            best = PromoPair(donor=candidates[0], target=target, hop=hop)
            break

        if best is None:
            orphans.append(
                OrphanTarget(
                    target=target,
                    reason=(
                        "все подходящие доноры уже отработали в этом слоте"
                        if reachable_any
                        else "нет сильного соседа ни на первом, ни на втором хопе"
                    ),
                )
            )
            continue

        pairs.append(best)
        used_donor_groups.add(best.donor.group_id)

        if len(pairs) >= max_pairs:
            break

    return pairs, orphans
