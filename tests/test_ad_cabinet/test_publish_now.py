"""Тесты кнопки «Опубликовать сейчас» (бесплатная моментальная публикация заявки).

Эндпоинт publish_request_now — интеграционный (VK/БД), тут покрываем чистую
ветку хелпера загрузки фото `_upload_request_photos`: без community-токена / без
URL / с мусором → пустой список (пост уйдёт текстом, без сети).
"""

import database.models  # noqa: F401
import database.models_extended  # noqa: F401
from web.api.ad_cabinet import _upload_request_photos


def test_no_community_token_returns_empty():
    # есть URL, но нет токена группы → загружать нечем
    assert _upload_request_photos(-123, {}, ["https://vk.com/photo.jpg"]) == []
    assert _upload_request_photos(-123, None, ["https://vk.com/photo.jpg"]) == []


def test_no_urls_returns_empty():
    assert _upload_request_photos(-123, {123: "tok"}, []) == []
    assert _upload_request_photos(-123, {123: "tok"}, None) == []


def test_non_http_urls_filtered():
    # нет валидных http(s)-URL → пусто, до сети не доходим
    assert _upload_request_photos(-123, {123: "tok"}, ["not-a-url", "", None]) == []
