"""Реакция раскрутки на коды ошибок VK.

Ровно эти коды и не обрабатывает сейчас никто: `_VK_EXPECTED_ERROR_CODES` в
клиенте — {15, 18, 203, 212, 220}, и 9/14/214/219 туда не входят. Для парсинга
это терпимо, для модуля, который публикует сам, — нет: это ранний сигнал бана.
"""

from modules.promotion.vk_errors import classify_promo_error, extract_vk_error_code, is_stop_signal


class TestExtractCode:
    def test_reads_code_from_publisher_message(self):
        # publisher поднимает наружу строку и теряет целочисленный код.
        message = "VK API error: [219] Advertisement post was recently added"
        assert extract_vk_error_code(message) == 219

    def test_none_without_code(self):
        assert extract_vk_error_code("Captcha needed") is None

    def test_none_on_empty(self):
        assert extract_vk_error_code("") is None
        assert extract_vk_error_code(None) is None

    def test_takes_first_bracketed_number(self):
        assert extract_vk_error_code("[9] flood [214] other") == 9


class TestStopSignals:
    def test_flood_control_stops_whole_module_for_a_day(self):
        action = classify_promo_error(9)
        assert action.kind == "stop_module"
        assert action.module_cooldown_seconds == 24 * 3600
        assert action.alert is True
        assert is_stop_signal(action)

    def test_captcha_pauses_module_for_six_hours(self):
        action = classify_promo_error(14)
        assert action.kind == "stop_module"
        assert action.module_cooldown_seconds == 6 * 3600
        assert is_stop_signal(action)

    def test_captcha_without_code_is_recognised_by_text(self):
        # Копи-сетка видит капчу именно текстом, без кода. Не распознать её
        # из-за формы записи — значит проглядеть самый частый анти-бот сигнал.
        action = classify_promo_error(None, "Captcha needed")
        assert action.kind == "stop_module"


class TestDonorLevelErrors:
    def test_posting_denied_blacklists_donor_for_a_day(self):
        action = classify_promo_error(214)
        assert action.kind == "blacklist_donor"
        assert action.blacklist_hours == 24
        assert action.alert is False  # рутина, будить владельца незачем
        assert not is_stop_signal(action)

    def test_ad_post_limit_blacklists_for_a_week_and_alerts(self):
        # 219 — это «я прочитал твой пост как рекламу». Повторять через час
        # бессмысленно: надо переписать шаблон, и владелец должен об этом узнать.
        action = classify_promo_error(219)
        assert action.kind == "blacklist_donor"
        assert action.blacklist_hours == 7 * 24
        assert action.alert is True

    def test_post_limit_blacklists_for_a_day(self):
        action = classify_promo_error(220)
        assert action.kind == "blacklist_donor"
        assert action.blacklist_hours == 24


class TestDelegatedAndUnknown:
    def test_token_codes_are_left_to_the_router(self):
        # 5/10/17/27/29 ведёт каскад токенов. Продублировать его решения здесь —
        # верный способ развести две правды об одном токене.
        for code in (5, 10, 15, 17, 27, 29):
            action = classify_promo_error(code)
            assert action.kind == "retry"
            assert action.blacklist_hours == 0
            assert action.module_cooldown_seconds == 0

    def test_unknown_code_does_not_stop_anything(self):
        action = classify_promo_error(1234)
        assert action.kind == "retry"
        assert not is_stop_signal(action)

    def test_error_without_code_is_retryable(self):
        action = classify_promo_error(None, "connection reset")
        assert action.kind == "retry"

    def test_code_can_be_taken_from_message(self):
        action = classify_promo_error(None, "VK API error: [9] Flood control")
        assert action.kind == "stop_module"
