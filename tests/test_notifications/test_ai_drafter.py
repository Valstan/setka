"""Tests for AI-drafted reply suggestions (etap 4b)."""

from unittest.mock import MagicMock, patch

from modules.notifications import ai_drafter


async def test_empty_text_short_circuits_without_calling_groq():
    """Whitespace-only input → graceful failure, no API call."""
    with patch.object(ai_drafter, "GROQ_API_KEY", "sk-test"):
        with patch("groq.Groq") as g:
            result = await ai_drafter.draft_comment_reply(original_text="   ")
    assert result["success"] is False
    assert "empty" in result["error"].lower()
    g.assert_not_called()


async def test_missing_api_key_returns_clear_error():
    """Operator-friendly error so the UI can show a useful hint."""
    with patch.object(ai_drafter, "GROQ_API_KEY", None):
        result = await ai_drafter.draft_comment_reply(original_text="hi")
    assert result["success"] is False
    assert "GROQ_API_KEY" in result["error"]


async def test_happy_path_returns_trimmed_draft():
    """Groq responds → we trim and forward the text + model id."""
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="  Спасибо за обращение!  "))]

    with patch.object(ai_drafter, "GROQ_API_KEY", "sk-test"):
        with patch("groq.Groq") as g:
            g.return_value.chat.completions.create.return_value = fake_completion
            result = await ai_drafter.draft_comment_reply(
                original_text="когда уберут снег с улиц?",
                region_name="МАЛМЫЖ - ИНФО",
                style="friendly",
            )

    assert result["success"] is True
    assert result["draft"] == "Спасибо за обращение!"
    assert result["model"]


async def test_empty_ai_response_is_treated_as_failure():
    """Whitespace from the model → failure, not a useless empty draft."""
    fake_completion = MagicMock()
    fake_completion.choices = [MagicMock(message=MagicMock(content="   "))]

    with patch.object(ai_drafter, "GROQ_API_KEY", "sk-test"):
        with patch("groq.Groq") as g:
            g.return_value.chat.completions.create.return_value = fake_completion
            result = await ai_drafter.draft_comment_reply(original_text="?")

    assert result["success"] is False
    assert "empty" in result["error"].lower()


async def test_groq_exception_is_caught_and_formatted():
    """Network / 429 / etc → return {success: False, error}, never raise."""
    with patch.object(ai_drafter, "GROQ_API_KEY", "sk-test"):
        with patch("groq.Groq") as g:
            g.return_value.chat.completions.create.side_effect = RuntimeError("429 rate limit")
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
