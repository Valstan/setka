"""Схема вердикта классификатора (ADR-0003 §B).

Pydantic-валидация входящих вердиктов от облачной рутины (и позже — от
Claude API structured output). В shadow ``theme`` — свободная строка (решение
владельца): учимся, какие темы нейронка рождает, до закрепления таксономии.
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

# Типы аспектов вердикта, по которым считаем agree-rate раздельно (ADR-0003 §F).
VERDICT_TYPES = ("theme", "action", "merge")

ACTIONS = ("publish", "delete", "hold")


class ClassifierVerdict(BaseModel):
    """Вердикт по одному посту."""

    post_id: int
    theme: str = Field(min_length=1, max_length=100)
    action: str = Field(default="hold")
    merge_with: List[int] = Field(default_factory=list)
    split: bool = False
    confidence: int = Field(default=0, ge=0, le=100)
    reasoning: str = Field(default="", max_length=500)
    model: Optional[str] = None

    def normalized_action(self) -> str:
        a = (self.action or "").strip().lower()
        return a if a in ACTIONS else "hold"

    def has_merge_signal(self) -> bool:
        """Есть ли у вердикта суждение о склейке (merge/split) — для agree-rate типа merge."""
        return bool(self.merge_with) or bool(self.split)

    def to_verdict_json(self) -> dict:
        """Сериализация в JSONB-колонку ``content_classifications.verdict``."""
        return {
            "theme": self.theme.strip(),
            "action": self.normalized_action(),
            "merge_with": list(self.merge_with),
            "split": bool(self.split),
            "confidence": int(self.confidence),
            "reasoning": (self.reasoning or "").strip(),
        }


class VerdictBatch(BaseModel):
    """Пакет вердиктов, который POST'ит облачная рутина."""

    verdicts: List[ClassifierVerdict]
