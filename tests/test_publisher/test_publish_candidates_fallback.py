"""Tests: VKPublisher fallback по списку publish-кандидатов.

Имитируем сценарий: Valstan возвращает VK error 5 (invalid_token / banned),
VKPublisher должен:
  1. Получить ошибку 5 на первом кандидате.
  2. Сообщить ``policy.report_error('VALSTAN', 5)`` (если policy задан).
  3. Выкинуть Valstan из локального списка кандидатов.
  4. Попробовать следующего кандидата (OLGA) и успешно опубликовать.

И отдельно — что при пустом списке кандидатов wall.repost (USER_WRITE)
бросает понятный RuntimeError, а не пытается обратиться к None client'у.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from modules.publisher.vk_publisher_extended import VKPublisher


def _client_returning(responses):
    """Клиент с очередью ответов через api_call(method, params)."""
    client = MagicMock(spec=["api_call"])
    seq = iter(responses)

    def _call(method, params):
        return next(seq)

    client.api_call.side_effect = _call
    return client


def _vk_error(code: int, msg: str = "fake"):
    return {"error": {"error_code": code, "error_msg": msg}}


@pytest.mark.asyncio
async def test_rotates_publish_token_on_code_5(monkeypatch):
    """Valstan 5 → следующий (OLGA) — успех."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}

    valstan_client = _client_returning([_vk_error(5, "User authorization failed")])
    olga_client = _client_returning([{"response": {"post_id": 999}}])

    publisher._user_clients = {"VALSTAN": valstan_client, "OLGA": olga_client}
    publisher._publish_candidates = [("VALSTAN", "tok_v"), ("OLGA", "tok_o")]
    publisher._active_publish_name = "VALSTAN"
    publisher.vk_client = valstan_client

    policy = MagicMock()
    policy.report_error = AsyncMock()
    policy.report_success = AsyncMock()
    publisher._policy = policy

    # Имитируем wall.repost (USER_TOKEN_ONLY) — попадаем в _try_publish_candidates.
    response, via = await publisher._call_wall_post(
        params={"object": "wall-1_2"},
        method="wall.repost",
    )

    assert response == {"post_id": 999}
    assert via == "publish-token:OLGA"
    policy.report_error.assert_awaited_once_with("VALSTAN", 5)
    policy.report_success.assert_awaited_once_with("OLGA")
    assert publisher._active_publish_name == "OLGA"


@pytest.mark.asyncio
async def test_raises_when_no_candidates_left():
    """Пустой список кандидатов → понятный RuntimeError, а не AttributeError."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}
    publisher._user_clients = {}
    publisher._publish_candidates = []
    publisher._active_publish_name = None
    publisher.vk_client = None
    publisher._policy = None

    with pytest.raises(RuntimeError, match="no publish-token available"):
        await publisher._call_wall_post(
            params={"object": "wall-1_2"},
            method="wall.repost",
        )


@pytest.mark.asyncio
async def test_all_candidates_fail_propagates_last_error():
    """Все кандидаты упали с code 5 → последняя ошибка пробрасывается."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}

    c1 = _client_returning([_vk_error(5, "valstan dead")])
    c2 = _client_returning([_vk_error(17, "olga validation")])

    publisher._user_clients = {"VALSTAN": c1, "OLGA": c2}
    publisher._publish_candidates = [("VALSTAN", "tv"), ("OLGA", "to")]
    publisher._active_publish_name = "VALSTAN"
    publisher.vk_client = c1

    policy = MagicMock()
    policy.report_error = AsyncMock()
    policy.report_success = AsyncMock()
    publisher._policy = policy

    with pytest.raises(Exception) as ei:
        await publisher._call_wall_post(
            params={"object": "wall-1_2"},
            method="wall.repost",
        )
    assert "olga validation" in str(ei.value)
    # report_error звался дважды — для каждого упавшего кандидата.
    assert policy.report_error.await_count == 2


@pytest.mark.asyncio
async def test_community_fallback_uses_candidates_when_available():
    """wall.post через community-token упал с 15 → переход на publish-candidates,
    не на legacy self.vk_client. Это значит fallback запишется как
    ``community-fallback-publish:OLGA`` (NAME присутствует — публикация через
    кандидата, не через legacy)."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}

    community_client = _client_returning([_vk_error(15, "access denied")])
    olga_client = _client_returning([{"response": {"post_id": 777}}])

    publisher._user_clients = {"OLGA": olga_client}
    publisher._publish_candidates = [("OLGA", "tok_o")]
    publisher._active_publish_name = None
    publisher.vk_client = None
    publisher._policy = None

    response, via = await publisher._call_wall_post(
        params={"owner_id": -1, "message": "x"},
        method="wall.post",
        client=community_client,
    )
    assert response == {"post_id": 777}
    assert via == "community-fallback-publish:OLGA"
