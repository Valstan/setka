"""Unit tests for VKPublisher group ID normalization."""

from types import SimpleNamespace

import pytest

# Ensure project root is importable when pytest runs outside configured PYTHONPATH.
from modules.publisher.vk_publisher_extended import VKPublisher


class _DummyVkClient:
    def __init__(self, group_info_response=None):
        self.calls = []
        self._group_info_response = group_info_response

    def api_call(self, method, params):
        self.calls.append((method, params))
        if method == "wall.repost":
            return {"response": {"success": 1, "post_id": 777}}
        if method == "groups.getById":
            return {"response": self._group_info_response}
        return {"response": {"post_id": 555}}


def test_normalize_group_owner_id():
    assert VKPublisher._normalize_group_owner_id(-12345) == -12345
    assert VKPublisher._normalize_group_owner_id(12345) == -12345


@pytest.mark.asyncio
async def test_publish_bulletin_normalizes_positive_group_id():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_bulletin(
        group_id=12345,
        text="digest text",
        attachments=[],
    )

    assert result["success"] is True
    assert vk.calls, "Expected VK API call to be made"
    method, params = vk.calls[0]
    assert method == "wall.post"
    assert params["owner_id"] == -12345


@pytest.mark.asyncio
async def test_publish_repost_normalizes_positive_group_id_and_sets_group_id():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    result = await publisher.publish_repost(
        group_id=12345,
        source_owner_id=-111,
        source_post_id=222,
    )

    assert result["success"] is True
    assert vk.calls, "Expected VK API call to be made"
    method, params = vk.calls[0]
    assert method == "wall.repost"
    assert params["group_id"] == 12345
    assert params["object"] == "wall-111_222"


# --------------------------------------------------------------------------- #
# get_group_info — миграция из старого vk_publisher.py
# --------------------------------------------------------------------------- #


_GROUP_PAYLOAD = {
    "id": 123,
    "name": "Тестовая группа",
    "screen_name": "test_group",
    "type": "group",
}


@pytest.mark.asyncio
async def test_get_group_info_success_list_response():
    """Старый VK API возвращал голый список (без обёртки groups)."""
    vk = _DummyVkClient(group_info_response=[_GROUP_PAYLOAD])
    publisher = VKPublisher(vk_client=vk)

    info = await publisher.get_group_info(123)

    assert info == {
        "id": 123,
        "name": "Тестовая группа",
        "screen_name": "test_group",
        "type": "group",
        "url": "https://vk.com/test_group",
    }
    method, params = vk.calls[0]
    assert method == "groups.getById"
    # VK API: plural `group_ids` as string (singular `group_id` deprecated → [100]).
    assert params == {"group_ids": "123"}


@pytest.mark.asyncio
async def test_get_group_info_success_dict_with_groups_key():
    """Новый VK API оборачивает результат в {'groups': [...]}.

    Метод должен корректно распаковать оба варианта.
    """
    vk = _DummyVkClient(group_info_response={"groups": [_GROUP_PAYLOAD]})
    publisher = VKPublisher(vk_client=vk)

    info = await publisher.get_group_info(123)

    assert info is not None
    assert info["name"] == "Тестовая группа"


@pytest.mark.asyncio
async def test_get_group_info_normalizes_negative_id():
    """VK groups.getById ждёт positive id — отрицательный должен быть приведён."""
    vk = _DummyVkClient(group_info_response=[_GROUP_PAYLOAD])
    publisher = VKPublisher(vk_client=vk)

    await publisher.get_group_info(-456)

    _, params = vk.calls[0]
    assert params == {"group_ids": "456"}


@pytest.mark.asyncio
async def test_get_group_info_zero_short_circuits_without_vk_call():
    """``group_id=0`` (env-var unset) → None без VK-вызова.

    Иначе VK отвечает [100] group_ids is undefined — ошибка ожидаемая
    и нечего на неё тратить round-trip. Сюда попадает кейс прода 2026-05-26,
    когда `VK_TEST_GROUP_ID` env не было задано → status endpoint выглядел
    как «inactive» на ровном месте.
    """
    vk = _DummyVkClient(group_info_response=[_GROUP_PAYLOAD])
    publisher = VKPublisher(vk_client=vk)

    info = await publisher.get_group_info(0)

    assert info is None
    assert vk.calls == []  # никакого VK-вызова


@pytest.mark.asyncio
async def test_get_group_info_returns_none_on_empty():
    vk = _DummyVkClient(group_info_response=[])
    publisher = VKPublisher(vk_client=vk)

    info = await publisher.get_group_info(123)

    assert info is None


@pytest.mark.asyncio
async def test_get_group_info_returns_none_on_exception():
    """Любая ошибка VK — None, никогда не raise (контракт совместим со старым).

    Endpoint /api/publisher/groups итерирует по всем production-группам и
    бросать здесь — значит ронять весь список из-за одной недоступной группы.
    """

    class _FailingClient:
        def api_call(self, method, params):
            raise RuntimeError("boom")

    publisher = VKPublisher(vk_client=_FailingClient())

    info = await publisher.get_group_info(123)

    assert info is None


# --------------------------------------------------------------------------- #
# get_target_group_id — staticmethod, ходит в RegionConfigManager
# --------------------------------------------------------------------------- #


def test_get_target_group_id_test_mode_returns_test_polygon(monkeypatch):
    """mode='test' → группа тест-полигона (id под ключом 'test' в RegionConfig)."""
    calls = []

    def fake_get_main_group_id(region_code):
        calls.append(region_code)
        return -137760500 if region_code == "test" else -111

    monkeypatch.setattr(
        "modules.region_config.RegionConfigManager.get_main_group_id",
        staticmethod(fake_get_main_group_id),
    )

    gid = VKPublisher.get_target_group_id("mi", "test")

    assert gid == -137760500
    assert calls == ["test"]  # передаваемый region_code игнорируется в test mode


def test_get_target_group_id_production_mode_returns_region_main(monkeypatch):
    monkeypatch.setattr(
        "modules.region_config.RegionConfigManager.get_main_group_id",
        staticmethod(lambda region_code: -200 if region_code == "mi" else None),
    )

    gid = VKPublisher.get_target_group_id("mi", "production")

    assert gid == -200


def test_get_target_group_id_unknown_region_returns_none(monkeypatch):
    """Регион не настроен — None (endpoint вернёт 404, не упадёт)."""
    monkeypatch.setattr(
        "modules.region_config.RegionConfigManager.get_main_group_id",
        staticmethod(lambda region_code: None),
    )

    assert VKPublisher.get_target_group_id("ghost", "production") is None


# --------------------------------------------------------------------------- #
# publish_aggregated_post — wrapper над publish_bulletin
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_publish_aggregated_post_publishes_digest_text():
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    digest = SimpleNamespace(
        aggregated_text="📰 Сводка дня",
        sources_count=3,
        total_views=100,
        total_likes=10,
    )

    result = await publisher.publish_aggregated_post(digest, -555)

    assert result["success"] is True
    assert result["owner_id"] == -555
    method, params = vk.calls[0]
    assert method == "wall.post"
    assert params["owner_id"] == -555
    assert params["message"] == "📰 Сводка дня"


@pytest.mark.asyncio
async def test_publish_aggregated_post_normalizes_positive_group_id():
    """Принимает положительный group_id — внутри уходит как -id."""
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    digest = SimpleNamespace(aggregated_text="x", sources_count=1)

    await publisher.publish_aggregated_post(digest, 555)

    _, params = vk.calls[0]
    assert params["owner_id"] == -555


@pytest.mark.asyncio
async def test_publish_aggregated_post_handles_missing_aggregated_text():
    """digest без `aggregated_text` — success=False, не raise."""
    vk = _DummyVkClient()
    publisher = VKPublisher(vk_client=vk)

    digest_without_text = SimpleNamespace(sources_count=1)

    result = await publisher.publish_aggregated_post(digest_without_text, -555)

    assert result["success"] is False
    assert "aggregated_text" in result["error"]
    assert vk.calls == []  # ни одного VK-вызова
