"""Оператору — одна фраза, машине — прежний формат строки.

07.09 человек прочёл «Ошибка публикации: Публикация не удалась: VK API error:
Response code 502». Шесть слоёв обёрток, три из них говорят одно и то же, и ни
один не отвечает, что делать.

Развязка тут несимметричная, и в ней вся суть: **строку исключения трогать
нельзя**. ``str(VKPublishError)`` обязан остаться ``VK API error: [<код>]
<текст>`` — по нему ``modules/promotion/vk_errors.extract_vk_error_code``
вынимает код регуляркой, и он же ложится в ``error_message`` строк
планировщика. Поэтому чинится не исключение, а то, что показывают человеку.
Последний тест файла — гвоздь ровно в это место.
"""

import pytest

from modules.promotion.vk_errors import extract_vk_error_code
from modules.publisher.vk_publisher_extended import VKPublishError
from web.api.ad_cabinet import _publish_failure_detail


class TestHumanSentence:
    def test_known_code_names_the_code_and_drops_the_service_prefix(self):
        res = {
            "success": False,
            "error": "VK API error: [214] Access to adding post denied",
            "vk_error_code": 214,
        }
        detail = _publish_failure_detail(res)

        assert detail.startswith("ВКонтакте отклонил публикацию (код 214).")
        assert "VK API error" not in detail, "служебный префикс человеку ничего не говорит"
        assert "Access to adding post denied" in detail, "текст ВК не выбрасываем"

    def test_without_code_the_phrase_still_reads_as_a_sentence(self):
        res = {"success": False, "error": "VK API error: something odd", "vk_error_code": None}
        assert _publish_failure_detail(res) == "Публикация не удалась. something odd"

    def test_empty_error_does_not_produce_a_dangling_phrase(self):
        assert _publish_failure_detail({"success": False}) == "Публикация не удалась."

    def test_no_double_prefix_anywhere_in_the_result(self):
        """Гвоздь: ни одного повторного «не удалась/ошибка» в одной фразе."""
        res = {
            "success": False,
            "error": "VK API error: [219] Advertisement post was recently added",
            "vk_error_code": 219,
        }
        detail = _publish_failure_detail(res)
        assert detail.count("не удалась") == 0
        assert detail.lower().count("ошибка") == 0


class TestMachineFormatIsUntouched:
    """Гвоздь всего файла: формат строки исключения остался прежним.

    Если кто-то «заодно улучшит сообщение» в ``VKPublishError``, раскрутка
    молча перестанет видеть коды — она достаёт их регуляркой из текста.
    """

    def test_str_of_publish_error_is_byte_for_byte_the_old_format(self):
        exc = VKPublishError(219, "[219] Advertisement post was recently added")
        assert str(exc) == "VK API error: [219] Advertisement post was recently added"

    def test_promotion_still_extracts_the_code_from_that_string(self):
        exc = VKPublishError(219, "[219] Advertisement post was recently added")
        assert extract_vk_error_code(str(exc)) == 219

    @pytest.mark.parametrize("transient", [True, False])
    def test_transient_flag_does_not_leak_into_the_string(self, transient):
        exc = VKPublishError(0, "Response code 502", transient=transient)
        assert str(exc) == "VK API error: Response code 502"
        assert exc.transient is transient
