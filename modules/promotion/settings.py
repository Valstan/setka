"""Состояние каналов раскрутки: выключен / сухой прогон / боевой.

Три независимых уровня гасят публикацию, и каждый следующий сильнее предыдущего:

1. ``PROMO_DISABLED`` в env — перекрывает всё. Пока взведён, любой канал работает
   как сухой прогон, что бы ни стояло в настройках.
2. ``paused_until`` в БД — пауза после того, как ВК ответил кодом 9 или 14.
   Живёт в БД, а не в Redis, потому что Redis-квоты у нас fail-open, а «ВК велел
   замолчать» — не то место, где допустимо продолжить при недоступном кэше.
3. ``channels[<канал>]`` — переключатель владельца: ``enabled`` и ``dry_run``.

**Новый канал всегда заводится сухим.** Не из вежливости: планировщик подбирает
пары сам, и канал, включённый по недосмотру, начал бы публиковать на живые стены
раньше, чем владелец увидел, что именно он собрался написать.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional

# Каналы и их состояние по умолчанию. Порядок словаря — порядок обхода
# диспетчером: сперва оформление (разовое), потом промо, потом областной дайджест.
DEFAULT_CHANNELS: Dict[str, Dict[str, Any]] = {
    "setup": {"enabled": False, "dry_run": True},
    "pin": {"enabled": False, "dry_run": True},
    "footer": {"enabled": False, "dry_run": True},
    "promo_post": {"enabled": True, "dry_run": True},
    "oblast_digest": {"enabled": True, "dry_run": True},
    "outreach": {"enabled": True, "dry_run": True},
}

# Каналы, которые публикует диспетчер. Остальные живут в своих прогонах
# (оформление — разовое, футер — внутри сборки сводки).
DISPATCH_CHANNELS = ("promo_post", "oblast_digest")


@dataclass(frozen=True)
class ChannelState:
    """Итоговое состояние канала после наложения всех трёх уровней."""

    name: str
    enabled: bool
    dry_run: bool
    reason: str = ""

    @property
    def publishes(self) -> bool:
        """Уйдёт ли реальный пост в ВК."""
        return self.enabled and not self.dry_run


def merge_channels(stored: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Слить настройки из БД с дефолтами. Незнакомые каналы отбрасываются.

    Отбрасываем намеренно: канал, которого нет в коде, не может быть включён
    записью в JSON — иначе опечатка в настройках создавала бы «канал-призрак»,
    видимый в UI и ничего не делающий.
    """
    merged: Dict[str, Dict[str, Any]] = {}
    stored = stored if isinstance(stored, dict) else {}
    for name, defaults in DEFAULT_CHANNELS.items():
        row = stored.get(name)
        row = row if isinstance(row, dict) else {}
        merged[name] = {
            "enabled": bool(row.get("enabled", defaults["enabled"])),
            "dry_run": bool(row.get("dry_run", defaults["dry_run"])),
        }
    return merged


def module_paused(paused_until: Optional[datetime], *, now: Optional[datetime] = None) -> bool:
    """На паузе ли модуль. ``None`` = не на паузе."""
    if paused_until is None:
        return False
    return (now or datetime.utcnow()) < paused_until


def resolve_channel(
    name: str,
    channels: Dict[str, Any],
    *,
    module_disabled: bool,
    paused: bool = False,
) -> ChannelState:
    """Свести три уровня в одно состояние канала.

    Порядок проверок — от сильного к слабому, и он же порядок причин в UI:
    сначала env-килл-свитч, потом пауза после ответа ВК, потом переключатель.
    """
    row = merge_channels(channels).get(name, {"enabled": False, "dry_run": True})
    enabled = bool(row["enabled"])
    dry_run = bool(row["dry_run"])

    if module_disabled:
        return ChannelState(
            name=name,
            enabled=enabled,
            dry_run=True,
            reason="PROMO_DISABLED в env — публикация выключена целиком",
        )
    if paused:
        return ChannelState(
            name=name,
            enabled=enabled,
            dry_run=True,
            reason="модуль на паузе после ответа ВК (код 9 или 14)",
        )
    if not enabled:
        return ChannelState(name=name, enabled=False, dry_run=True, reason="канал выключен")
    if dry_run:
        return ChannelState(
            name=name, enabled=True, dry_run=True, reason="сухой прогон: пишем план, не публикуем"
        )
    return ChannelState(name=name, enabled=True, dry_run=False, reason="боевой режим")
