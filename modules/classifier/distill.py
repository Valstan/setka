"""Дистилляция коррекций оператора в правила — звено 7 петли обучения (D-024).

**Место в цепочке.** ИИ-фильтр выносит вердикты по правилам → оператор правит их
в ленте `/classifier` → *здесь* правки обобщаются в новые правила → правила
уходят в постулаты и меняют поведение фильтра. Без этого звена петля разомкнута:
оператор правит одно и то же вечно, потому что модель не учится.

**Чем это было до сих пор.** Облачная рутина-дистиллятор (удалена владельцем
2026-07-14), после неё — ручной прогон из чата по памятке ``/distill``. То есть
самое медленное звено зависело от того, сядет ли человек за компьютер. Мандат
D-024 переводит его на прямой DeepSeek тем же паттерном, что ИИ-фильтр.

**Что дистиллятор НЕ делает — и это главное свойство.** Он не применяет правила.
Предложения ложатся со статусом ``proposed`` и ждут оператора в ленте (ADR-0005,
человек в петле). Автоприменение здесь было бы худшим из возможных решений:
правило пишется по нескольким правкам, действует на весь поток и меняет то, что
читают люди в 26 районах. Цена ошибки несимметрична — поэтому предлагает машина,
решает человек.

**Порог осмысленности.** Меньше ``MIN_CORRECTIONS`` правок — не дистиллируем.
Правило, выведенное из двух случаев, это не обобщение, а запись частного случая
в общий закон; такие правила потом приходится выводить из обращения. Тот же
порог, что в памятке ``/distill``.

**Обобщённость проверяется явно.** Модель охотно пишет «пост про ремонт дороги на
Ленина → publish» — это не правило, а пересказ одной правки. Просим формулировать
признак, по которому решение переносится на будущие посты, и отбрасываем
предложения, буквально цитирующие текст поста из сырья.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from modules.classifier.schema import RuleProposal
from modules.deepseek_client import chat

logger = logging.getLogger(__name__)

# Меньше этого — сырья нет (порог из памятки /distill).
MIN_CORRECTIONS = 10
# Сколько правок уходит в один вызов. Дистилляция — задача на весь массив сразу:
# правило рождается из ПОВТОРЯЮЩЕГОСЯ паттерна, а нарезка на чанки прячет повтор.
MAX_CORRECTIONS = 200
_MAX_TOKENS = 2500
# Сколько правил максимум за прогон. Больше — признак того, что модель пишет
# под каждый случай, а не обобщает.
MAX_PROPOSALS = 8

TASK_PROMPT = (
    """Ты — методист классификатора районных новостных лент.

Тебе дают: (1) действующие правила, (2) список правок оператора — случаи, где
живой редактор НЕ СОГЛАСИЛСЯ с решением ИИ и поставил своё.

Твоя работа — найти ПОВТОРЯЮЩИЕСЯ паттерны в правках и предложить обобщённые
правила, которые в будущем избавят оператора от этой работы.

ЧТО ТАКОЕ ХОРОШЕЕ ПРАВИЛО:
- описывает ПРИЗНАК, по которому решение переносится на будущие посты
  («частные объявления о продаже техники и авто → delete»), а не пересказывает
  один случай («пост про продажу Лады 2007 → delete»);
- опирается минимум на 2-3 правки из списка, а не на одну;
- не противоречит действующим правилам; если противоречит — так и напиши в
  rationale, это важнее, чем красивое правило.

ЧЕГО НЕ ДЕЛАТЬ:
- не предлагай правило, уже покрытое действующими (перечитай их список);
- не переписывай текст поста в правило;
- лучше 2 хороших правила, чем 8 натянутых. Нет устойчивого паттерна — верни
  пустой список, это нормальный и полезный ответ.

Ответ — СТРОГО JSON, без markdown-обёртки:
{"proposals": [{"rule_text": "...", "rationale": "почему, одна фраза",
"evidence_lips": ["lip1", "lip2"], "region_code": null}]}

`region_code` — код района, если паттерн районный; null — если правило общее.
Правил не больше %d.

=== ДЕЙСТВУЮЩИЕ ПРАВИЛА ===
"""
    % MAX_PROPOSALS
)


def build_system_prompt(postulates: str) -> str:
    return TASK_PROMPT + (postulates or "").strip()


def build_user_prompt(corrections: Sequence[Dict[str, Any]]) -> str:
    """Правки оператора: что решил ИИ, что поставил человек, и текст поста."""
    parts: List[str] = []
    for i, c in enumerate(corrections, 1):
        parts.append(
            "\n".join(
                [
                    f"### Правка {i}",
                    f"lip: {c.get('lip')}",
                    f"регион: {c.get('region_code') or '—'}",
                    f"аспект: {c.get('verdict_type')}",
                    f"ИИ поставил: {c.get('ai_value')!r}",
                    f"оператор исправил на: {c.get('operator_value')!r}",
                    f"текст поста: {str(c.get('post_text') or '')[:600]}",
                ]
            )
        )
    return "\n\n".join(parts)


def _extract_json(content: str) -> Optional[Dict[str, Any]]:
    raw = (content or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _looks_like_a_retelling(rule_text: str, corrections: Sequence[Dict[str, Any]]) -> bool:
    """Правило пересказывает конкретный пост, а не обобщает?

    Признак — длинный дословный кусок текста поста внутри правила. Порог 40
    символов: короткие совпадения («частные объявления») это нормальная лексика
    предметной области, а сорокасимвольная цитата — уже переписанный пост.
    """
    text = re.sub(r"\s+", " ", (rule_text or "")).strip().lower()
    if not text:
        return True
    for c in corrections:
        post = re.sub(r"\s+", " ", str(c.get("post_text") or "")).strip().lower()
        for start in range(0, max(0, len(post) - 40), 20):
            fragment = post[start : start + 40]
            if len(fragment) == 40 and fragment in text:
                return True
    return False


def parse_proposals(
    payload: Dict[str, Any],
    corrections: Sequence[Dict[str, Any]],
    *,
    model: Optional[str] = None,
) -> Tuple[List[RuleProposal], List[str]]:
    """Разобрать ответ в предложения правил. Возвращает ``(правила, отбраковка)``."""
    raw_list = payload.get("proposals")
    if not isinstance(raw_list, list):
        return [], ["ответ без списка proposals"]

    known_lips = {str(c.get("lip")) for c in corrections}
    out: List[RuleProposal] = []
    rejected: List[str] = []

    for raw in raw_list[:MAX_PROPOSALS]:
        if not isinstance(raw, dict):
            rejected.append("не-объект в списке")
            continue
        rule_text = str(raw.get("rule_text") or "").strip()
        if len(rule_text) < 10:
            rejected.append("пустое или слишком короткое правило")
            continue
        if _looks_like_a_retelling(rule_text, corrections):
            rejected.append(f"пересказ конкретного поста: {rule_text[:60]}…")
            continue
        # Доказательная база — только lip'ы из поданного сырья: правило, чьи
        # «доказательства» ссылаются на неизвестные посты, оператор проверить не
        # сможет, а значит и оценить тоже.
        evidence_lips = raw.get("evidence_lips")
        if not isinstance(evidence_lips, (list, tuple)):
            evidence_lips = []
        evidence = [{"lip": str(x)} for x in evidence_lips if str(x) in known_lips][:20]
        region = raw.get("region_code")
        out.append(
            RuleProposal(
                rule_text=rule_text[:600],
                rationale=str(raw.get("rationale") or "").strip()[:500],
                evidence=evidence,
                region_code=(str(region)[:50] if region else None),
                model=model,
            )
        )
    return out, rejected


def distill(
    corrections: Sequence[Dict[str, Any]],
    *,
    postulates: str,
    min_corrections: int = MIN_CORRECTIONS,
    model: Optional[str] = None,
) -> Dict[str, Any]:
    """Обобщить правки в предложения правил. Никогда не бросает.

    ``reason='not-enough-material'`` — не ошибка, а нормальный исход: правок
    мало, обобщать нечего. Так же нормален пустой список предложений при
    достаточном сырье — значит устойчивого паттерна нет.
    """
    if len(corrections) < min_corrections:
        return {
            "ok": True,
            "reason": "not-enough-material",
            "proposals": [],
            "corrections": len(corrections),
            "rejected": [],
            "tokens": 0,
        }

    material = list(corrections)[:MAX_CORRECTIONS]
    result = chat(
        system=build_system_prompt(postulates),
        user=build_user_prompt(material),
        temperature=0.3,  # чуть выше, чем у фильтра: обобщение — не рубрикация
        max_tokens=_MAX_TOKENS,
        json_object=True,
        model=model,
        label="distill",
    )
    usage = result.get("usage") or {}
    tokens = int(usage.get("total_tokens") or 0)
    if not result.get("ok"):
        return {
            "ok": False,
            "reason": result.get("reason"),
            "detail": result.get("detail"),
            "proposals": [],
            "corrections": len(material),
            "rejected": [],
            "tokens": tokens,
        }

    payload = _extract_json(str(result.get("content") or ""))
    if payload is None:
        return {
            "ok": False,
            "reason": "llm_unparseable",
            "proposals": [],
            "corrections": len(material),
            "rejected": [],
            "tokens": tokens,
        }

    proposals, rejected = parse_proposals(
        payload, material, model=str(result.get("model") or "")[:100] or None
    )
    return {
        "ok": True,
        "reason": "distilled",
        "proposals": proposals,
        "corrections": len(material),
        "rejected": rejected,
        "tokens": tokens,
    }


def summarize(run: Dict[str, Any]) -> str:
    return "corrections=%s proposals=%s rejected=%s tokens=%s reason=%s" % (
        run.get("corrections"),
        len(run.get("proposals") or []),
        len(run.get("rejected") or []),
        run.get("tokens"),
        run.get("reason"),
    )
