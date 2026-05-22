"""AI-assisted drafting of replies to VK comments (etap 4b).

Thin wrapper around Groq Cloud API. The model receives the original
comment plus optional context (region name, tone hint) and produces a
short, neutral, community-appropriate draft. The operator is expected
to edit before sending — this is a starting point, not a final answer.

Returns a uniform `{success, draft, model}` / `{success: False, error}`
shape so the API endpoint can pass it through to the frontend without
extra mapping.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from config.runtime import GROQ_API_KEY

logger = logging.getLogger(__name__)


# Keep replies short — long auto-generated text reads as bot-like and
# rarely matches the operator's intent. 400 tokens ≈ 5–7 русских предложений.
_MAX_TOKENS = 400
_TEMPERATURE = 0.6
_MODEL = "llama-3.1-8b-instant"


_STYLE_HINTS = {
    "short": "Ответ ОЧЕНЬ короткий — одна-две фразы.",
    "friendly": "Тон дружелюбный, тёплый, но без панибратства.",
    "formal": "Тон официальный, нейтральный, без эмодзи.",
}


def _build_prompt(
    *,
    original_text: str,
    region_name: Optional[str],
    style: Optional[str],
) -> str:
    region_part = f"Сообщество: «{region_name}». " if region_name else ""
    style_hint = _STYLE_HINTS.get((style or "").strip().lower(), _STYLE_HINTS["friendly"])
    return (
        "Ты — администратор регионального новостного сообщества во ВКонтакте. "
        "Тебе нужно ответить на комментарий читателя от имени сообщества. "
        f"{region_part}{style_hint} "
        "Не повторяй текст комментария. Не добавляй приветствие, если в нём нет вопроса. "
        "Не используй смайлики, если комментарий нейтральный или критический. "
        "Не давай обещаний от лица администрации (например, не пиши «исправим», "
        "«сделаем», «передадим в управу») — только то, что сообщество реально может "
        "сказать: поблагодарить, уточнить, направить к источнику.\n\n"
        f"Комментарий читателя:\n«{(original_text or '').strip()[:1500]}»\n\n"
        "Напиши только текст ответа, без префиксов вроде «Ответ:» или «Администратор:»."
    )


async def draft_comment_reply(
    *,
    original_text: str,
    region_name: Optional[str] = None,
    style: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask Groq for a draft reply. Returns dict described in the module docstring.

    Failure modes are translated to `{success: False, error}` instead of
    raising — the UI displays the error inline and lets the operator type
    their own reply.
    """
    if not GROQ_API_KEY:
        return {"success": False, "error": "GROQ_API_KEY is not configured"}

    if not (original_text or "").strip():
        return {"success": False, "error": "original_text is empty"}

    try:
        from groq import Groq
    except ImportError:
        return {"success": False, "error": "groq SDK not installed"}

    prompt = _build_prompt(
        original_text=original_text,
        region_name=region_name,
        style=style,
    )

    try:
        client = Groq(api_key=GROQ_API_KEY)
        # groq SDK is sync — run in thread so we don't block the event loop.
        completion = await asyncio.to_thread(
            client.chat.completions.create,
            model=_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=_TEMPERATURE,
            max_tokens=_MAX_TOKENS,
        )
        content = (completion.choices[0].message.content or "").strip()
        if not content:
            return {"success": False, "error": "empty AI response"}
        return {"success": True, "draft": content, "model": _MODEL}
    except Exception as e:
        logger.warning("Groq draft failed: %s", e)
        return {"success": False, "error": str(e)}
