"""AI-assisted drafting of replies to VK comments (etap 4b).

Thin wrapper around the shared DeepSeek client. The model receives the original
comment plus optional context (region name, tone hint) and produces a short,
neutral, community-appropriate draft. The operator is expected to edit before
sending — this is a starting point, not a final answer.

Returns a uniform `{success, draft, model}` / `{success: False, error}`
shape so the API endpoint can pass it through to the frontend without
extra mapping.

**Движок — DeepSeek** (мандат brain 2026-08-10, решение владельца D-024). Был
Groq; его ключ вдобавок скомпрометирован и держался рабочим только ради двух
потребителей, из которых этот — второй и последний.

Clipboard fallback сохранён без изменений: при любом отказе (нет ключа,
сеть, квота) словарь несёт `prompt` — тот самый текст, который оператор
вставит в свой ChatGPT/Claude. Фронт кладёт его в буфер обмена, и функция
остаётся рабочей при нулевом бюджете API. Именно поэтому отказ здесь —
деградация, а не ошибка: путь через человека предусмотрен с самого начала.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from modules.deepseek_client import chat

logger = logging.getLogger(__name__)


# Keep replies short — long auto-generated text reads as bot-like and
# rarely matches the operator's intent. 400 tokens ≈ 5–7 русских предложений.
_MAX_TOKENS = 400
_TEMPERATURE = 0.6


_STYLE_HINTS = {
    "short": "Ответ ОЧЕНЬ короткий — одна-две фразы.",
    "friendly": "Тон дружелюбный, тёплый, но без панибратства.",
    "formal": "Тон официальный, нейтральный, без эмодзи.",
}


def build_draft_prompt(
    *,
    original_text: str,
    region_name: Optional[str],
    style: Optional[str],
) -> str:
    """Build the LLM prompt. Public so the clipboard fallback can reuse it."""
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


# Backward-compatible alias — earlier code/tests referenced the private name.
_build_prompt = build_draft_prompt


async def draft_comment_reply(
    *,
    original_text: str,
    region_name: Optional[str] = None,
    style: Optional[str] = None,
) -> Dict[str, Any]:
    """Ask DeepSeek for a draft reply. Returns dict described in the module docstring.

    Failure modes are translated to `{success: False, error, prompt}` instead
    of raising — the UI displays the error inline, offers the prompt for the
    clipboard, and lets the operator type their own reply.
    """
    if not (original_text or "").strip():
        # No text → nothing to draft and no useful prompt to hand back.
        return {"success": False, "error": "original_text is empty"}

    # Built once up front so every failure branch can attach it for the
    # clipboard fallback (operator pastes it into their own LLM).
    prompt = build_draft_prompt(
        original_text=original_text,
        region_name=region_name,
        style=style,
    )

    # Клиент синхронный (urllib) — уводим в поток, чтобы не блокировать event loop.
    result = await asyncio.to_thread(
        chat,
        user=prompt,
        temperature=_TEMPERATURE,
        max_tokens=_MAX_TOKENS,
    )
    if not result.get("ok"):
        reason = str(result.get("reason") or "unknown")
        if reason != "no_api_key":
            logger.warning("DeepSeek draft failed: %s", reason)
        error = "DEEPSEEK_API_KEY is not configured" if reason == "no_api_key" else reason
        return {"success": False, "error": error, "prompt": prompt}

    return {
        "success": True,
        "draft": str(result.get("content") or ""),
        "model": result.get("model"),
    }
