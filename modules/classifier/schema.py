"""Схема вердикта классификатора (ADR-0003 §B).

Pydantic-валидация вердиктов от облачной рутины (и позже — от Claude API).
Ключ поста — ``lip`` ("<owner_abs>_<post_id>", структурный фингерпринт), т.к.
активный конвейер не пишет Post-строки — источник постов свод­ки
(``bulletin_curation_runs.candidates``). В shadow ``theme`` — свободная строка.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# Типы аспектов вердикта, по которым считаем agree-rate раздельно (ADR-0003 §F).
VERDICT_TYPES = ("theme", "action", "merge")

ACTIONS = ("publish", "delete", "hold")


class ClassifierVerdict(BaseModel):
    """Вердикт по одному посту (ключ — lip)."""

    lip: str = Field(min_length=1, max_length=50)
    theme: str = Field(min_length=1, max_length=100)
    action: str = Field(default="hold")
    merge_with: List[str] = Field(default_factory=list)  # lip'ы постов для склейки
    split: bool = False
    confidence: int = Field(default=0, ge=0, le=100)
    reasoning: str = Field(default="", max_length=500)
    # Практическая срочность для жителей: отключения света/воды/тепла, перекрытия,
    # ремонт коммуникаций, официальные предупреждения об опасности. Решение
    # владельца 2026-09-22: такой пост идёт в ленту вперёд рейтинга — у свежего
    # сообщения об аварии просто нет времени набрать просмотры, а нужно оно
    # людям именно сейчас. Криминал, ДТП, война, смерти срочными НЕ считаются.
    urgent: bool = False
    # Значимость новости для района, 0..100. Пока только хранится и печатается:
    # на порядок публикации не влияет, чтобы у срочности был один смысл и один
    # эффект. Копится как сырьё под будущее решение владельца.
    importance: Optional[int] = Field(default=None, ge=0, le=100)
    # Что модель увидела во вложениях (для постов без текста): «афиша концерта
    # в ДК 20 июля». Показывается оператору в ленте рядом с вердиктом.
    media_summary: str = Field(default="", max_length=500)
    model: Optional[str] = None
    # Эхо снапшота из /pending (рутина возвращает — чтобы сохранить текст/url,
    # кандидат в свод­ке транзиентен). Опциональны: если пусто, сервер добирает.
    text: str = Field(default="", max_length=10000)
    url: str = Field(default="", max_length=300)
    region_code: str = Field(default="", max_length=50)

    def normalized_action(self) -> str:
        a = (self.action or "").strip().lower()
        return a if a in ACTIONS else "hold"

    def normalized_theme(self) -> str:
        """Тема без названия действия внутри.

        У ``action`` словарь закрытый, у ``theme`` его нет вовсе — она свободная
        строка, и движок этим пользуется: кладёт в неё ``hold``/``delete``, и
        оно едет в Корпус как полноценная тема. Перестановки полей в коде при
        этом нет ни на одном пути — дефект в отсутствующей валидации, поэтому
        чинится он здесь, на сериализации, а не поиском «виновника».

        Тема обнуляется, а вердикт остаётся: его ``action`` управляет
        публикацией, и терять вердикт целиком было бы дороже пустой темы —
        пустую весь код уже умеет показывать («?» в ленте, «—» в пачке).
        """
        theme = (self.theme or "").strip()
        return "" if theme.casefold() in ACTIONS else theme

    def has_merge_signal(self) -> bool:
        """Есть ли у вердикта суждение о склейке (merge/split) — для agree-rate типа merge."""
        return bool(self.merge_with) or bool(self.split)

    def to_verdict_json(self) -> dict:
        """Сериализация в JSONB-колонку ``content_classifications.verdict``."""
        out = {
            "theme": self.normalized_theme(),
            "action": self.normalized_action(),
            "merge_with": [str(x) for x in self.merge_with],
            "split": bool(self.split),
            "confidence": int(self.confidence),
            "reasoning": (self.reasoning or "").strip(),
            # Пишется ВСЕГДА, в том числе False: читатели вердиктов
            # (``selection.fetch_urgent_lips``) должны отличать «модель сказала
            # не срочно» от «поле из старой версии схемы отсутствует».
            "urgent": bool(self.urgent),
        }
        if self.importance is not None:
            out["importance"] = int(self.importance)
        if (self.media_summary or "").strip():
            out["media_summary"] = self.media_summary.strip()
        return out


def parse_verdict_loose(raw: object) -> Optional[ClassifierVerdict]:
    """Толерантный разбор одного вердикта: чинить, что чинится, не роняя батч.

    Прогон рутины стоит токенов; строгий 422 на ВЕСЬ батч из-за одного
    перелимита (эхо длинного текста поста, разросшийся reasoning) выбрасывал
    результаты целого прогона. Здесь мягкая нормализация: строки обрезаем до
    лимитов схемы, confidence зажимаем в 0..100, мусорные типы приводим.
    None — только если вердикт нечинимый (нет lip или theme).
    """
    if not isinstance(raw, dict):
        return None

    def _s(key: str, cap: int) -> str:
        v = raw.get(key)
        return str(v).strip()[:cap] if v is not None else ""

    lip = _s("lip", 50)
    theme = _s("theme", 100)
    if not lip or not theme:
        return None
    try:
        confidence = max(0, min(100, int(raw.get("confidence") or 0)))
    except (TypeError, ValueError):
        confidence = 0
    merge_with = raw.get("merge_with")
    if not isinstance(merge_with, (list, tuple)):
        merge_with = []

    # Модель возвращает то булев `true`, то строку «true»/«да» — второе не
    # исключение, а обычное поведение LLM на JSON без строгой схемы. Пустое
    # поле (старый вердикт, промах модели) читается как «не срочно»: вперёд
    # рейтинга пост подвинет только явное «да».
    raw_urgent = raw.get("urgent")
    if isinstance(raw_urgent, bool):
        urgent = raw_urgent
    elif raw_urgent is None:
        urgent = False
    else:
        urgent = str(raw_urgent).strip().casefold() in ("true", "1", "да", "yes", "y")

    importance = raw.get("importance")
    if importance is None:
        importance_val = None
    else:
        try:
            importance_val = max(0, min(100, int(importance)))
        except (TypeError, ValueError):
            importance_val = None

    return ClassifierVerdict(
        lip=lip,
        theme=theme,
        action=_s("action", 20) or "hold",
        merge_with=[str(x)[:50] for x in merge_with][:50],
        split=bool(raw.get("split")),
        confidence=confidence,
        reasoning=_s("reasoning", 500),
        urgent=urgent,
        importance=importance_val,
        media_summary=_s("media_summary", 500),
        model=(_s("model", 100) or None),
        text=_s("text", 10000),
        url=_s("url", 300),
        region_code=_s("region_code", 50),
    )


class RuleProposal(BaseModel):
    """Черновик выученного правила, который POST'ит рутина дистилляции (ADR-0005).

    Обобщённое правило (не построчный лог), выведенное из накопленных коррекций
    оператора. Применяется только после утверждения оператором в ленте.
    """

    rule_text: str = Field(min_length=3, max_length=600)
    rationale: str = Field(default="", max_length=500)  # почему предложено (1 строка)
    # доказательная база — какие коррекции породили (для суждения оператора)
    evidence: List[dict] = Field(default_factory=list)
    region_code: Optional[str] = Field(default=None, max_length=50)  # None = глобальное
    model: Optional[str] = None


class RuleProposalBatch(BaseModel):
    """Пакет черновиков правил от рутины дистилляции."""

    proposals: List[RuleProposal]
