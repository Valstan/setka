"""Tests for AI-drafted reply suggestions (etap 4b).

Движок — DeepSeek через общий ``modules.deepseek_client`` (D-024, 2026-08-12).
Подменяется ``ai_drafter.chat``; раньше подменялся класс ``groq.Groq`` и вместе
с ним MagicMock-цепочка ``chat.completions.create``.

Главное свойство, которое эти тесты стерегут, от смены движка не изменилось:
**при любом отказе словарь несёт ``prompt``.** На нём держится clipboard
fallback — оператор вставляет промпт в свой ChatGPT/Claude и работает дальше,
когда API недоступен. Отказ здесь — деградация, а не ошибка.
"""

from modules.notifications import ai_drafter


def _install_chat(monkeypatch, result):
    """Подменить вызов модели; вернуть счётчик вызовов и захваченные kwargs."""
    state = {"calls": 0, "kwargs": {}}

    def fake_chat(**kwargs):
        state["calls"] += 1
        state["kwargs"] = kwargs
        return result

    monkeypatch.setattr(ai_drafter, "chat", fake_chat)
    return state


def _ok(content: str, model: str = "deepseek-chat"):
    return {"ok": True, "content": content, "model": model, "usage": None}


async def test_empty_text_short_circuits_without_calling_model(monkeypatch):
    """Whitespace-only input → graceful failure, no API call."""
    state = _install_chat(monkeypatch, _ok("не должно вызваться"))
    result = await ai_drafter.draft_comment_reply(original_text="   ")
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    assert state["calls"] == 0


async def test_missing_api_key_returns_clear_error(monkeypatch):
    """Operator-friendly error so the UI can show a useful hint."""
    _install_chat(monkeypatch, {"ok": False, "reason": "no_api_key"})
    result = await ai_drafter.draft_comment_reply(original_text="hi")
    assert result["success"] is False
    assert "DEEPSEEK_API_KEY" in result["error"]


async def test_missing_api_key_carries_prompt_for_clipboard_fallback(monkeypatch):
    """No budget → still hand back the prompt so the UI offers clipboard copy."""
    _install_chat(monkeypatch, {"ok": False, "reason": "no_api_key"})
    result = await ai_drafter.draft_comment_reply(
        original_text="когда уберут снег?",
        region_name="МАЛМЫЖ - ИНФО",
    )
    assert result["success"] is False
    assert "когда уберут снег?" in result["prompt"]
    assert "МАЛМЫЖ - ИНФО" in result["prompt"]


async def test_empty_text_has_no_prompt_in_fallback(monkeypatch):
    """Empty input → no draftable prompt to fall back to."""
    _install_chat(monkeypatch, {"ok": False, "reason": "no_api_key"})
    result = await ai_drafter.draft_comment_reply(original_text="   ")
    assert result["success"] is False
    assert "prompt" not in result


async def test_network_failure_carries_prompt_for_fallback(monkeypatch):
    """Quota/network failure → prompt is attached so the operator can copy it."""
    _install_chat(monkeypatch, {"ok": False, "reason": "http_403", "detail": "forbidden"})
    result = await ai_drafter.draft_comment_reply(original_text="вопрос?")
    assert result["success"] is False
    assert "403" in result["error"]
    assert "вопрос?" in result["prompt"]


async def test_happy_path_returns_draft_and_model(monkeypatch):
    """Модель ответила → отдаём текст и фактическое имя модели."""
    state = _install_chat(monkeypatch, _ok("Спасибо за обращение!"))
    result = await ai_drafter.draft_comment_reply(
        original_text="когда уберут снег с улиц?",
        region_name="МАЛМЫЖ - ИНФО",
        style="friendly",
    )
    assert result["success"] is True
    assert result["draft"] == "Спасибо за обращение!"
    assert result["model"] == "deepseek-chat"
    # Черновик — свободный текст, JSON-режим тут не нужен и только мешал бы.
    assert state["kwargs"].get("json_object") in (None, False)
    assert state["kwargs"]["temperature"] == ai_drafter._TEMPERATURE


async def test_empty_ai_response_is_treated_as_failure(monkeypatch):
    """Пустой ответ модели → отказ, а не бесполезный пустой черновик."""
    _install_chat(monkeypatch, {"ok": False, "reason": "empty_response"})
    result = await ai_drafter.draft_comment_reply(original_text="?")
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    assert "prompt" in result


async def test_rate_limit_is_reported_not_raised(monkeypatch):
    """Network / 429 / etc → return {success: False, error}, never raise."""
    _install_chat(monkeypatch, {"ok": False, "reason": "http_429"})
    result = await ai_drafter.draft_comment_reply(original_text="?")
    assert result["success"] is False
    assert "429" in result["error"]


def test_prompt_includes_region_name_and_style():
    """Region name and friendly hint should reach the model in the prompt."""
    prompt = ai_drafter._build_prompt(
        original_text="??",
        region_name="МАЛМЫЖ - ИНФО",
        style="friendly",
    )
    assert "МАЛМЫЖ - ИНФО" in prompt
    assert "дружелюбный" in prompt.lower()


def test_prompt_falls_back_to_friendly_for_unknown_style():
    """Unrecognised style string defaults to 'friendly' rather than blank."""
    prompt = ai_drafter._build_prompt(
        original_text="x",
        region_name=None,
        style="rude-mode",
    )
    assert "дружелюбный" in prompt.lower()


def test_public_prompt_builder_is_aliased():
    """The clipboard fallback relies on the public name; alias kept for back-compat."""
    assert ai_drafter.build_draft_prompt is ai_drafter._build_prompt
