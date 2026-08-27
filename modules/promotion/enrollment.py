"""Кого брать в раскрутку и кого из неё выпускать — чистая логика.

Правило простое: район с малым числом подписчиков зачисляется, выросший —
выпускается. Две тонкости, из-за которых это не однострочник.

**Гистерезис.** Порог входа (300) и порог выхода (400) разные намеренно. При
равных район, топчущийся на границе, входил бы и выходил каждую ночь, дёргая
планировщик и засоряя журнал; хуже того, «выпустился» перестало бы что-либо
значить в отчёте.

**Отсутствие снимка — не «много подписчиков».** Суна, Кумёны и Зуевка
активированы 27.08 и суточного снимка ещё не имеют. Прочитать ``None`` как «выше
порога» значило бы не заметить ровно те районы, ради которых модуль и пишется,
поэтому район без снимка зачисляется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence


@dataclass(frozen=True)
class RegionState:
    """Состояние района на момент решения.

    ``members`` = ``None`` — снимка ещё не было (район активирован сегодня).
    ``enrollment_status`` = ``None`` — район в раскрутке ни разу не был.
    """

    region_id: int
    code: str
    kind: str
    is_active: bool
    has_group: bool
    members: Optional[int]
    enrollment_status: Optional[str] = None


@dataclass(frozen=True)
class EnrollmentDecision:
    """Что делаем с районом: ``enroll`` | ``graduate`` | ``keep``."""

    region_id: int
    code: str
    action: str
    members: Optional[int]
    reason: str


def _eligible(state: RegionState, allowlist: Sequence[str]) -> bool:
    """Годится ли район в раскрутку в принципе.

    Только районы: у области и страны источники — стены детей, и «подписчики»
    там значат другое. Без ``vk_group_id`` продвигать нечего — сообщества нет.
    """
    if state.kind != "raion" or not state.is_active or not state.has_group:
        return False
    if allowlist and state.code not in allowlist:
        return False
    return True


def evaluate_enrollment(
    states: Iterable[RegionState],
    *,
    threshold_members: int = 300,
    graduate_members: int = 400,
    allowlist: Sequence[str] = (),
    now: Optional[datetime] = None,
) -> List[EnrollmentDecision]:
    """Решить судьбу каждого района.

    Args:
        states: состояния районов.
        threshold_members: вход — строго меньше этого числа подписчиков.
        graduate_members: выход — не меньше этого числа. Должен быть больше
            порога входа; если передали меньше, он поднимается до входного,
            иначе гистерезис вырождается в мигание.
        allowlist: коды районов для обкатки; пусто = без фильтра.
        now: не используется в расчёте, принимается для единообразия сигнатур
            и тестируемости вызывающего кода.

    Returns:
        Решения только по тем районам, где есть что делать (``keep`` попадает в
        список лишь для уже зачисленных — чтобы вызывающий видел полный состав).
    """
    graduate_at = max(graduate_members, threshold_members)
    decisions: List[EnrollmentDecision] = []

    for state in states:
        enrolled = state.enrollment_status == "active"

        if not _eligible(state, allowlist):
            if enrolled:
                decisions.append(
                    EnrollmentDecision(
                        region_id=state.region_id,
                        code=state.code,
                        action="graduate",
                        members=state.members,
                        reason="район больше не подходит под условия раскрутки",
                    )
                )
            continue

        if state.enrollment_status == "graduated":
            # Выпустившийся район назад автоматически не возвращаем: возврат —
            # это решение владельца, а не следствие того, что кто-то отписался.
            continue

        if state.enrollment_status == "paused":
            continue

        if state.members is None:
            if not enrolled:
                decisions.append(
                    EnrollmentDecision(
                        region_id=state.region_id,
                        code=state.code,
                        action="enroll",
                        members=None,
                        reason="снимка подписчиков ещё нет — район только заведён",
                    )
                )
            else:
                decisions.append(
                    EnrollmentDecision(
                        region_id=state.region_id,
                        code=state.code,
                        action="keep",
                        members=None,
                        reason="снимка подписчиков нет",
                    )
                )
            continue

        if enrolled:
            if state.members >= graduate_at:
                decisions.append(
                    EnrollmentDecision(
                        region_id=state.region_id,
                        code=state.code,
                        action="graduate",
                        members=state.members,
                        reason=f"подписчиков {state.members} ≥ порога выхода {graduate_at}",
                    )
                )
            else:
                decisions.append(
                    EnrollmentDecision(
                        region_id=state.region_id,
                        code=state.code,
                        action="keep",
                        members=state.members,
                        reason=f"подписчиков {state.members}",
                    )
                )
            continue

        if state.members < threshold_members:
            decisions.append(
                EnrollmentDecision(
                    region_id=state.region_id,
                    code=state.code,
                    action="enroll",
                    members=state.members,
                    reason=f"подписчиков {state.members} < порога входа {threshold_members}",
                )
            )

    return decisions


def summarize(decisions: Sequence[EnrollmentDecision]) -> Dict[str, int]:
    """Свести решения в счётчики для статуса задачи и логов."""
    counts = {"enroll": 0, "graduate": 0, "keep": 0}
    for decision in decisions:
        if decision.action in counts:
            counts[decision.action] += 1
    return counts
