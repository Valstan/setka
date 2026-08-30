"""Тесты VK-обёрток канала setup (моки vk_api) и хелперов оркестратора."""

from unittest.mock import MagicMock, patch

from modules.promotion.branding import COVER_H, COVER_W
from modules.promotion.group_setup_vk import (
    edit_description,
    get_current,
    pin_post,
    post_welcome,
    upload_avatar,
    upload_cover,
)


class VkError(Exception):
    def __init__(self, code, msg="vk error"):
        super().__init__(msg)
        self.code = code


def _api(**methods):
    """Мок vk_api.get_api(): api.groups.edit и т.п."""
    api = MagicMock()
    for path, side in methods.items():
        node = api
        parts = path.split(".")
        for p in parts[:-1]:
            node = getattr(node, p)
        target = getattr(node, parts[-1])
        if isinstance(side, Exception):
            target.side_effect = side
        else:
            target.return_value = side
    return api


def test_get_current_snapshot():
    api = _api(
        **{
            "groups.getById": [
                {
                    "description": "текст",
                    "status": "",
                    "city": {"id": 1, "title": "Калинино"},
                    "has_photo": 0,
                    "cover": {"enabled": 0},
                    "screen_name": "x_info43",
                }
            ]
        }
    )
    res = get_current(api, -123)
    assert res.ok
    assert res.payload["city"] == "Калинино"
    assert res.payload["has_cover"] is False


def test_get_current_error_code_extracted():
    api = _api(**{"groups.getById": VkError(27)})
    res = get_current(api, 123)
    assert not res.ok and res.vk_error_code == 27


def test_edit_description_ok():
    api = _api(**{"groups.edit": 1})
    assert edit_description(api, 123, "новое").ok
    api.groups.edit.assert_called_once_with(group_id=123, description="новое")


def test_upload_avatar_full_cycle_and_system_post_cleanup():
    api = _api(
        **{
            "photos.getOwnerPhotoUploadServer": {"upload_url": "http://up"},
            "photos.saveOwnerPhoto": {"photo_id": 5},
            "wall.get": {
                "items": [
                    {"id": 42, "text": "", "attachments": [{"type": "photo"}]},
                    {"id": 41, "text": "новость", "attachments": []},
                ]
            },
            "wall.delete": 1,
        }
    )
    with patch("modules.promotion.group_setup_vk.requests.post") as up:
        up.return_value.json.return_value = {"server": 1, "hash": "h", "photo": "p"}
        res = upload_avatar(api, 123, b"jpeg")
    assert res.ok and "удалён" in res.detail
    api.wall.delete.assert_called_once_with(owner_id=-123, post_id=42)


def test_upload_avatar_cleanup_failure_is_not_fatal():
    """Аватар встал, чистка упала — это ok с пометкой, не провал."""
    api = _api(
        **{
            "photos.getOwnerPhotoUploadServer": {"upload_url": "http://up"},
            "photos.saveOwnerPhoto": {"photo_id": 5},
            "wall.get": VkError(6, "rate limit"),
        }
    )
    with patch("modules.promotion.group_setup_vk.requests.post") as up:
        up.return_value.json.return_value = {"server": 1, "hash": "h", "photo": "p"}
        res = upload_avatar(api, 123, b"jpeg")
    assert res.ok and "чистка не удалась" in res.detail


def test_upload_avatar_empty_upload_fails():
    api = _api(**{"photos.getOwnerPhotoUploadServer": {"upload_url": "http://up"}})
    with patch("modules.promotion.group_setup_vk.requests.post") as up:
        up.return_value.json.return_value = {"photo": ""}
        res = upload_avatar(api, 123, b"jpeg")
    assert not res.ok


def test_upload_cover_full_cycle():
    api = _api(
        **{
            "photos.getOwnerCoverPhotoUploadServer": {"upload_url": "http://up"},
            "photos.saveOwnerCoverPhoto": {"images": []},
        }
    )
    with patch("modules.promotion.group_setup_vk.requests.post") as up:
        up.return_value.json.return_value = {"hash": "h", "photo": "p"}
        assert upload_cover(api, 123, b"jpeg").ok
    kwargs = api.photos.getOwnerCoverPhotoUploadServer.call_args.kwargs
    # Раньше здесь стояли литералы 1590×400 — канон обложки ВК. Они и закрепляли
    # баг: картинка рисуется 2560×644, ВК вырезал из неё прямоугольник 1590×400
    # и отрезал 38% ширины, а тест это подтверждал (инцидент 2026-08-31, у десяти
    # сообществ заголовок обрывался на 71%). Сверяем с размером ХОЛСТА, а не с
    # числом: кроп обязан покрывать всё, что нарисовано.
    assert kwargs["crop_x2"] == COVER_W and kwargs["crop_y2"] == COVER_H
    assert kwargs["crop_x"] == 0 and kwargs["crop_y"] == 0


def test_post_welcome_returns_post_id():
    api = _api(**{"wall.post": {"post_id": 77}})
    res = post_welcome(api, 123, "текст")
    assert res.ok and res.payload["post_id"] == 77
    api.wall.post.assert_called_once_with(owner_id=-123, from_group=1, message="текст")


def test_pin_post_error_27():
    api = _api(**{"wall.pin": VkError(27)})
    res = pin_post(api, 123, 77)
    assert not res.ok and res.vk_error_code == 27


def test_genitive_from_zagolovki():
    import importlib.util
    import os

    spec = importlib.util.spec_from_file_location(
        "setup_groups_script",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert (
        mod._genitive_from_zagolovki({"novost": "Новости Опаринского округа:"})
        == "Опаринского округа"
    )
    assert mod._genitive_from_zagolovki({"novost": "что-то другое"}) is None
    assert mod._genitive_from_zagolovki(None) is None
