"""publish_suggested / pin_post / unpin_post идут ТОЛЬКО user-токеном.

Даже при наличии community-токена/кандидатов группы вызов не должен уходить
под community-auth: у предложенного поста и закрепа право только у user-токена
администратора (probe scripts/probe_suggested_wall_post.py, group_setup_vk).
"""

from __future__ import annotations

import asyncio

from modules.publisher.vk_publisher_extended import VKPublisher


class _FakeUserClient:
    def __init__(self):
        self.calls = []

    async def api_call(self, method, params):
        self.calls.append((method, dict(params)))
        if method == "wall.post":
            return {"post_id": 424242}
        return 1


class _FakeCommunityClient(_FakeUserClient):
    async def api_call(self, method, params):
        raise AssertionError(f"community client must not be used for {method}")


def _publisher():
    user = _FakeUserClient()
    pub = VKPublisher(vk_client=user, community_tokens={100: "community-token"})
    # Подменяем ленивый community-клиент двойником, который падает при любом вызове.
    pub._community_clients[100] = _FakeCommunityClient()
    return pub, user


def test_publish_suggested_uses_user_token_with_post_id_and_signature():
    pub, user = _publisher()
    res = asyncio.run(pub.publish_suggested(-100, 78276, signed=True, publish_date=1_800_000_000))
    assert res["success"] and res["post_id"] == 424242 and res["postponed"] is True
    assert len(user.calls) == 1
    method, params = user.calls[0]
    assert method == "wall.post"
    assert params == {
        "owner_id": -100,
        "post_id": 78276,
        "from_group": 1,
        "signed": 1,
        "publish_date": 1_800_000_000,
    }


def test_publish_suggested_without_publish_date_publishes_now():
    pub, user = _publisher()
    res = asyncio.run(pub.publish_suggested(100, 5, signed=False))
    assert res["success"] and res["postponed"] is False
    _method, params = user.calls[0]
    assert "publish_date" not in params and "signed" not in params
    assert params["owner_id"] == -100  # нормализация в отрицательный owner_id


def test_pin_and_unpin_bypass_community_token():
    pub, user = _publisher()
    assert asyncio.run(pub.pin_post(-100, 7))["success"]
    assert asyncio.run(pub.unpin_post(-100, 7))["success"]
    assert [m for m, _ in user.calls] == ["wall.pin", "wall.unpin"]
    assert all(p == {"owner_id": -100, "post_id": 7} for _, p in user.calls)


def test_publish_suggested_returns_vk_error_code():
    class _Boom(_FakeUserClient):
        async def api_call(self, method, params):
            return {"error": {"error_code": 15, "error_msg": "Access denied"}}

    boom = _Boom()
    pub = VKPublisher(vk_client=boom)
    res = asyncio.run(pub.publish_suggested(-100, 1))
    assert res["success"] is False and res["vk_error_code"] == 15
