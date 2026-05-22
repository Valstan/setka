"""Tests for the inline-keyboard helper in telegram_alert (etap 4b-4)."""

from modules.notifications import telegram_alert


def test_keyboard_always_has_primary_open_button():
    """Even with zero notifications, we still want a one-tap link to the cabinet."""
    kb = telegram_alert._build_reply_keyboard(
        "https://example/notifications",
        {"suggested_count": 0, "messages_count": 0, "comments_count": 0},
    )
    assert kb is not None
    rows = kb.inline_keyboard
    assert len(rows) == 1
    assert rows[0][0].text.startswith("📬")
    assert rows[0][0].url == "https://example/notifications"


def test_keyboard_adds_section_buttons_for_present_categories():
    """Only categories with count > 0 get their own deep-link button."""
    kb = telegram_alert._build_reply_keyboard(
        "https://example/notifications",
        {"suggested_count": 0, "messages_count": 3, "comments_count": 7},
    )
    rows = kb.inline_keyboard
    assert len(rows) == 2
    second_row = rows[1]
    labels = [b.text for b in second_row]
    urls = [b.url for b in second_row]

    # messages and comments should be present, suggested shouldn't
    assert any("Ответить" in label for label in labels)
    assert any("Комменты" in label for label in labels)
    assert not any("Предложки" in label for label in labels)

    assert any(u.endswith("#section=messages") for u in urls)
    assert any(u.endswith("#section=comments") for u in urls)


def test_keyboard_strips_trailing_hash_from_base_url():
    """Don't double up '#' if dashboard_url already ends with one."""
    kb = telegram_alert._build_reply_keyboard(
        "https://example/notifications#",
        {"messages_count": 1, "suggested_count": 0, "comments_count": 0},
    )
    second_row = kb.inline_keyboard[1]
    assert second_row[0].url == "https://example/notifications#section=messages"


def test_keyboard_button_count_reflects_quantities():
    """Counts are visible in button text so the operator knows the load."""
    kb = telegram_alert._build_reply_keyboard(
        "https://example/notifications",
        {"messages_count": 12, "comments_count": 3, "suggested_count": 5},
    )
    labels = [b.text for b in kb.inline_keyboard[1]]
    assert any("(12)" in label for label in labels)
    assert any("(3)" in label for label in labels)
    assert any("(5)" in label for label in labels)
