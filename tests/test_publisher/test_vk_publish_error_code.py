"""Код ошибки ВК доезжает до вызывающего — и инварианты, которые нельзя нарушать.

Корень техдолга «коды VK 9/14/214/219 не обрабатывает никто» оказался не в том,
что кто-то реагирует неправильно, а в том, что до реагирующего слоя целое число
вообще не доезжало: внутри публикатора код известен (``_VKApiCallError.code``),
но наружу поднималось голое ``Exception``, и в провальном словаре
``publish_bulletin`` кода не было. Единственный, кто добывал его обратно, —
раскрутка, регуляркой по тексту.

Здесь два вида тестов, и разделение намеренное:

* **краснеющие на старом коде** — что код теперь виден;
* **зелёные и сегодня** — инварианты, которые ЗАПРЕЩЕНО нарушать. Они написаны
  не для текущей правки, а для того, кто однажды решит «починить» техдолг
  одной строчкой в словаре роутера: 9 или 14 в
  ``_AUTO_DISABLE_CODES_HOURS`` отправят в cooldown общий publish-токен, и
  флуд-контроль ОДНОЙ стены погасит публикацию всей сети.
"""

from __future__ import annotations

import pytest

from modules.publisher.vk_publisher_extended import (
    _PUBLISH_ROTATE_CODES,
    _WALL_SCOPED_CODES,
    VKPublisher,
    VKPublishError,
    _vk_error_code_of,
)

# ───────── контракт строки: его ломать нельзя ─────────


def test_str_keeps_the_exact_legacy_format():
    """``str(...)`` обязан остаться прежним — от него зависят двое.

    ``web/api/ad_cabinet`` пишет строку в ``error_message`` карточки клиента, а
    ``modules/promotion/vk_errors`` вынимает из неё код регуляркой. Смена
    формата сломала бы раскрутку молча.
    """
    e = VKPublishError(219, "[219] Advertisement post was recently added")
    assert str(e) == "VK API error: [219] Advertisement post was recently added"
    assert str(e).startswith("VK API error: [219]")


def test_promo_regex_still_finds_the_code_in_the_string():
    """Тот же путь, которым раскрутка достаёт код сегодня, обязан работать."""
    from modules.promotion.vk_errors import extract_vk_error_code

    e = VKPublishError(9, "[9] Flood control")
    assert extract_vk_error_code(str(e)) == 9


def test_code_is_available_as_an_attribute():
    """Ради чего всё и делалось: код рядом со строкой, а не внутри неё."""
    e = VKPublishError(214, "[214] Access to adding post denied")
    assert e.code == 214
    assert e.message == "[214] Access to adding post denied"


# ───────── извлечение кода: ноль не выдаём за ответ ВК ─────────


def test_extractor_returns_none_for_unknown():
    """Ноль наружу не отдаём: ``code=0`` — это «ВК прислал ошибку без кода».

    Так приходит капча, и так же выглядят сетевые сбои. «VK код 0» в отчёте
    читался бы как настоящий ответ ВК — а это его отсутствие.
    """
    assert _vk_error_code_of(VKPublishError(0, "Captcha needed")) is None
    assert _vk_error_code_of(RuntimeError("сеть отвалилась")) is None
    assert _vk_error_code_of(ConnectionError()) is None


def test_extractor_returns_real_codes():
    for code in (9, 14, 214, 219, 220):
        assert _vk_error_code_of(VKPublishError(code, f"[{code}] x")) == code


# ───────── ИНВАРИАНТЫ. Зелёные сегодня, и обязаны такими остаться ─────────


@pytest.mark.parametrize("code", [9, 14, 214, 219])
def test_wall_and_flood_codes_never_disable_the_token(code: int):
    """9/14/214/219 НЕ должны попадать в auto-disable маршрутизатора.

    Цена ошибки: ``disabled_until`` ляжет на строку общего publish-аккаунта,
    ``_load_active`` выкинет его из ``pick()`` — и разом пропадут wall.repost в
    copy_setka, user-фолбэк у broadcast/krugozor/radar и публикация в районах
    без собственного community-токена. То есть флуд-контроль ОДНОЙ стены
    погасит сеть. Тест стоит здесь ровно затем, чтобы покраснеть у того, кто
    решит закрыть техдолг одной строчкой в словаре.
    """
    from modules.vk_token_router import _AUTO_DISABLE_CODES_HOURS

    assert code not in _AUTO_DISABLE_CODES_HOURS


@pytest.mark.parametrize("code", sorted(_WALL_SCOPED_CODES))
def test_wall_scoped_codes_are_never_rotated(code: int):
    """«Стенные» коды нельзя пробовать следующим токеном.

    Каскад community → МАМА → VALSTAN сделал бы три записи подряд в стену,
    которую ВК только что закрыл; а 219 — счётчик рекламных постов ИМЕННО на
    этой стене, и повтор с другого аккаунта его же и добивает.
    """
    assert code not in _PUBLISH_ROTATE_CODES
    assert code not in VKPublisher._COMMUNITY_FALLBACK_CODES


def test_wall_scoped_set_is_not_empty():
    """Сторож самого сторожа: пустое множество зеленило бы всё выше молча."""
    assert _WALL_SCOPED_CODES == frozenset({214, 219, 220})


# ───────── поведение: код доезжает до вызывающего (краснеет на старом коде) ─────────


def _client_returning(responses):
    """Клиент с очередью ответов через api_call — как в соседних тестах."""
    from unittest.mock import MagicMock

    client = MagicMock(spec=["api_call"])
    seq = iter(responses)
    client.api_call.side_effect = lambda method, params: next(seq)
    return client


def _bare_publisher(client):
    """VKPublisher без __init__ (не тянем env/токены), как в соседних тестах."""
    p = VKPublisher.__new__(VKPublisher)
    p.test_polygon_mode = False
    p.test_polygon_group_id = -137760500
    p._last_post_time = {}
    p._community_tokens = {}
    p._community_clients = {}
    p._user_clients = {"VALSTAN": client}
    p._publish_candidates = [("VALSTAN", "tok")]
    p._active_publish_name = "VALSTAN"
    p.vk_client = client
    p._policy = None
    return p


@pytest.mark.parametrize("code", [9, 214, 219])
@pytest.mark.asyncio
async def test_publish_bulletin_reports_vk_error_code(code: int):
    """Провальный словарь несёт числовой код.

    Краснеет на старом коде: ключа ``vk_error_code`` там нет вовсе, а наружу
    поднималось голое ``Exception`` без атрибута ``code``.
    """
    client = _client_returning([{"error": {"error_code": code, "error_msg": f"[{code}] nope"}}])
    publisher = _bare_publisher(client)

    res = await publisher.publish_bulletin(group_id=-123, text="привет")

    assert res["success"] is False
    assert res["vk_error_code"] == code
    # Строка не изменилась — на её формат завязаны ad_cabinet и раскрутка.
    assert res["error"].startswith(f"VK API error: [{code}]")


@pytest.mark.asyncio
async def test_publish_bulletin_reports_none_when_code_unknown():
    """Ошибка без кода (капча, сеть) → None, а не «код 0»."""
    client = _client_returning([{"error": {"error_msg": "Captcha needed"}}])
    publisher = _bare_publisher(client)

    res = await publisher.publish_bulletin(group_id=-123, text="привет")

    assert res["success"] is False
    assert res["vk_error_code"] is None
