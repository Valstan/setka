"""Groq-based categoriser for VK community discovery candidates.

Input: snapshot of a VK group (name, description, members_count, recent posts).
Output: `{success, category, confidence, reasoning, is_info_page}` —
serialisable straight into ``community_candidates`` columns.

Designed defensively: any failure (no API key, no SDK, network/429,
malformed JSON, empty answer) returns ``{success: False, error}`` and never
raises. The caller stores ``ai_category=None`` and lets the moderator pick
the category by hand in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any, Dict, Iterable, List, Optional

from config.runtime import GROQ_API_KEY

logger = logging.getLogger(__name__)

_MODEL = "llama-3.1-8b-instant"
_TEMPERATURE = 0.2  # категоризация — задача с одним «правильным» ответом
_MAX_TOKENS = 300

# Категории, которые модель должна выбирать. Совпадают с beat-schedule и
# Community.category. ``other`` — escape hatch для всего, что не подходит ни
# к одной теме (модератор решит, что с этим делать).
ALLOWED_CATEGORIES: tuple[str, ...] = (
    "admin",
    "novost",
    "reklama",
    "sosed",
    "kultura",
    "sport",
    "detsad",
    "other",
)


def _build_prompt(
    *,
    name: str,
    description: Optional[str],
    members_count: Optional[int],
    recent_posts: Iterable[str],
    region_name: Optional[str] = None,
) -> str:
    members_part = f" (~{members_count} подписчиков)" if members_count else ""
    region_part = (
        f"Регион (район), к которому хотим привязать сообщество: «{region_name}». "
        if region_name
        else ""
    )
    posts_block = "\n".join(
        f"- {p.strip()[:250]}" for p in list(recent_posts)[:5] if (p or "").strip()
    )
    if not posts_block:
        posts_block = "(нет доступных постов для анализа)"
    cats_list = ", ".join(ALLOWED_CATEGORIES)
    return (
        "Ты — модератор сети региональных пабликов ВКонтакте. Тебе показывают "
        "VK-сообщество, его кратко надо классифицировать по тематике. "
        f"{region_part}"
        f"Сообщество: «{(name or '').strip()}»{members_part}.\n"
        f"Описание: «{(description or '').strip()[:600] or '—'}».\n"
        f"Последние посты:\n{posts_block}\n\n"
        "Выбери одну категорию из этого списка (строго ровно одно слово):\n"
        f"  {cats_list}\n\n"
        "Где:\n"
        "  admin   — официальные органы (администрация района, депутаты, госорганы);\n"
        "  novost  — новостной паблик района/города;\n"
        "  reklama — объявления, барахолка, доска, куплю/продам;\n"
        "  sosed   — соседи, ДТП, происшествия, флуд жителей;\n"
        "  kultura — культура, афиша, дом культуры, библиотеки;\n"
        "  sport   — спорт, фитнес, секции, команды;\n"
        "  detsad  — детский сад, школа, родительские чаты;\n"
        "  other   — ничего из перечисленного не подходит.\n\n"
        "Также определи: похоже ли это сообщество на ГЛАВНУЮ ИНФО-страницу "
        "района (универсальный паблик, центр коммуникации, обычно тысячи "
        "подписчиков, постит и новости, и объявления). Если да — is_info_page=true.\n\n"
        "Верни ТОЛЬКО валидный JSON-объект, БЕЗ markdown-ограждений, "
        "БЕЗ префикса 'json':\n"
        '{"category": "<one of the list>", "confidence": <0-100 integer>, '
        '"is_info_page": <true|false>, "reasoning": "<одна короткая фраза>"}'
    )


def _strip_json_fences(text: str) -> str:
    """Если модель всё-таки обернула JSON в ```json ... ``` — снимем обёртку."""
    t = (text or "").strip()
    if t.startswith("```"):
        # remove leading ``` or ```json
        t = re.sub(r"^```(?:json)?\s*\n?", "", t)
        t = re.sub(r"\n?```\s*$", "", t)
    return t


def _parse_response(content: str) -> Optional[Dict[str, Any]]:
    """Robust JSON parsing — иногда LLM возвращает с лишним текстом до/после.

    Если сразу не парсится, пытаемся вытащить первое ``{ ... }``.
    """
    stripped = _strip_json_fences(content)
    try:
        return json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        pass
    match = re.search(r"\{.*\}", stripped, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _normalise(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """Clamp/sanitize модельный ответ. Возвращает то, что пойдёт в БД."""
    cat = str(parsed.get("category") or "").strip().lower()
    if cat not in ALLOWED_CATEGORIES:
        cat = "other"
    try:
        confidence = int(parsed.get("confidence") or 0)
    except (TypeError, ValueError):
        confidence = 0
    confidence = max(0, min(100, confidence))
    is_info = bool(parsed.get("is_info_page"))
    reasoning = str(parsed.get("reasoning") or "").strip()[:400] or None
    return {
        "category": cat,
        "confidence": confidence,
        "is_info_page": is_info,
        "reasoning": reasoning,
    }


async def categorize_candidate(
    *,
    name: str,
    description: Optional[str] = None,
    members_count: Optional[int] = None,
    recent_posts: Optional[List[str]] = None,
    region_name: Optional[str] = None,
) -> Dict[str, Any]:
    """Run Groq categorisation. Always returns a dict — never raises.

    Success: ``{success: True, category, confidence, is_info_page, reasoning,
              model}``.
    Failure: ``{success: False, error}``.
    """
    if not (name or "").strip():
        return {"success": False, "error": "name is empty"}
    if not GROQ_API_KEY:
        return {"success": False, "error": "GROQ_API_KEY is not configured"}
    try:
        from groq import Groq
    except ImportError:
        return {"success": False, "error": "groq SDK not installed"}

    prompt = _build_prompt(
        name=name,
        description=description,
        members_count=members_count,
        recent_posts=recent_posts or [],
        region_name=region_name,
    )

    try:
        client = Groq(api_key=GROQ_API_KEY)
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
        )
        content = (completion.choices[0].message.content or "").strip()
    except Exception as e:
        logger.warning("Groq categorize failed: %s", e)
        return {"success": False, "error": str(e)}

    if not content:
        return {"success": False, "error": "empty AI response"}

    parsed = _parse_response(content)
    if parsed is None:
        return {
            "success": False,
            "error": "AI response is not valid JSON",
            "raw": content[:300],
        }

    out = _normalise(parsed)
    out["success"] = True
    out["model"] = _MODEL
    return out
