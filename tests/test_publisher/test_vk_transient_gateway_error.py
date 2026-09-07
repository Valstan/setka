"""Отказ ШЛЮЗА ВК (HTTP 5xx) — отдельный класс, а не «ошибка без кода».

07.09 ВКонтакте отвечал 502/503/504 полторы минуты. Публикация объявления из
кабинета упала, и оператор увидел «Ошибка публикации: Публикация не удалась:
VK API error: Response code 502» — тупик, из которого не видно ни того, что
лежит сторона ВК, ни того, что достаточно повторить.

Корень был не в 502. ``ApiHttpError`` наследует ``VkApiError``, а **не**
``ApiError``, поэтому проваливался в generic-ветку ``VKClient.api_call`` и терял
всякий признак класса: наружу шёл голый текст без кода. А весь механизм
устойчивости публикатора — fallback на community-токен по 15/27 и ротация
publish-кандидата по 5/17/29 — сравнивает именно код. Ноль не совпадал ни с
чем, и до механизма управление просто не доходило.

Тесты ниже держат три свойства:

1. признак ``transient`` доезжает от библиотеки до вызывающего;
2. **чтение** после отказа шлюза повторяется молча;
3. **запись** молча НЕ повторяется — это защита от дубля (класс #218), а не
   недоделка: 5xx означает «шлюз не ответил», и ответ мог потеряться уже после
   того, как ВК запись принял.
"""

from unittest.mock import MagicMock

import pytest

from modules.publisher.vk_publisher_extended import (
    VKPublisher,
    _is_retry_safe_method,
    _VKApiCallError,
)


def _publisher(client):
    """VKPublisher без сетевого __init__ (та же схема, что в соседних тестах)."""
    publisher = VKPublisher.__new__(VKPublisher)
    publisher.vk_client = client
    publisher.test_polygon_mode = False
    publisher.test_polygon_group_id = -137760500
    publisher._last_post_time = {}
    publisher._community_tokens = {}
    publisher._community_clients = {}
    return publisher


def _client_returning(responses):
    """Клиент с ``api_call``, отдающий ответы по очереди."""
    it = iter(responses)
    client = MagicMock(spec=["api_call"])
    client.api_call.side_effect = lambda method, params: next(it)
    return client


GATEWAY_502 = {"error": {"error_msg": "Response code 502", "http_status": 502, "transient": True}}
VK_CODE_15 = {"error": {"error_code": 15, "error_msg": "Access denied"}}


class TestClassIsNotLost:
    """Звено 1: признак доезжает от api_call до вызывающего."""

    @pytest.mark.asyncio
    async def test_gateway_error_arrives_marked_transient(self):
        client = _client_returning([GATEWAY_502])
        publisher = _publisher(client)

        with pytest.raises(_VKApiCallError) as excinfo:
            await publisher._invoke_once(client, "wall.post", {"owner_id": -1})

        assert excinfo.value.transient is True
        assert excinfo.value.code == 0, "кода у HTTP-отказа нет и выдумывать его нельзя"

    @pytest.mark.asyncio
    async def test_ordinary_vk_error_is_not_transient(self):
        """Гвоздь: обычный ответ ВК с кодом не должен переехать в новый класс."""
        client = _client_returning([VK_CODE_15])
        publisher = _publisher(client)

        with pytest.raises(_VKApiCallError) as excinfo:
            await publisher._invoke_once(client, "wall.post", {"owner_id": -1})

        assert excinfo.value.code == 15
        assert excinfo.value.transient is False

    def test_captcha_shaped_zero_code_stays_distinguishable(self):
        """Код 0 бывает и у настоящего ответа ВК (капча) — это разные вещи."""
        captcha = _VKApiCallError(code=0, message="Captcha needed")
        gateway = _VKApiCallError(code=0, message="Response code 502", transient=True)
        assert captcha.transient is False
        assert gateway.transient is True


class TestReadsAreRetried:
    """Звено 2: чтение можно повторить молча — оно ничего не создаёт."""

    @pytest.mark.asyncio
    async def test_read_recovers_on_second_attempt(self, monkeypatch):
        monkeypatch.setattr("modules.publisher.vk_publisher_extended._TRANSIENT_BACKOFF_SECONDS", 0)
        client = _client_returning([GATEWAY_502, {"response": {"items": [1]}}])
        publisher = _publisher(client)

        got = await publisher._invoke(client, "wall.getById", {"posts": "-1_2"})

        assert got == {"items": [1]}
        assert client.api_call.call_count == 2

    @pytest.mark.asyncio
    async def test_read_gives_up_after_the_budget(self, monkeypatch):
        """Повторы конечны: ВК может лежать дольше, чем мы готовы ждать."""
        monkeypatch.setattr("modules.publisher.vk_publisher_extended._TRANSIENT_BACKOFF_SECONDS", 0)
        client = _client_returning([GATEWAY_502] * 3)
        publisher = _publisher(client)

        with pytest.raises(_VKApiCallError):
            await publisher._invoke(client, "wall.get", {"owner_id": -1})

        assert client.api_call.call_count == 3, "одна попытка + два повтора"

    @pytest.mark.asyncio
    async def test_coded_error_is_not_retried_even_on_a_read(self, monkeypatch):
        """Повтор — реакция на отсутствие ответа, а не на отказ по праву."""
        monkeypatch.setattr("modules.publisher.vk_publisher_extended._TRANSIENT_BACKOFF_SECONDS", 0)
        client = _client_returning([VK_CODE_15])
        publisher = _publisher(client)

        with pytest.raises(_VKApiCallError):
            await publisher._invoke(client, "wall.get", {"owner_id": -1})

        assert client.api_call.call_count == 1


class TestWritesAreNotRetried:
    """Звено 3 — гвоздь всего файла.

    Слепой повтор ``wall.post`` на 502 — это ровно механизм, который 27.08 дал
    один и тот же пост каждые полчаса в живом канале: инструмент отчитался
    неудачей, а сообщение ушло. Повтор записи остаётся за человеком, у которого
    перед глазами состояние.
    """

    @pytest.mark.asyncio
    async def test_wall_post_is_attempted_exactly_once(self):
        client = _client_returning([GATEWAY_502])
        publisher = _publisher(client)

        with pytest.raises(_VKApiCallError) as excinfo:
            await publisher._invoke(client, "wall.post", {"owner_id": -1})

        assert client.api_call.call_count == 1, "запись не повторяем — можно задвоить пост"
        assert excinfo.value.transient is True, "но признак наверх отдаём"

    @pytest.mark.parametrize(
        "method,safe",
        [
            ("wall.get", True),
            ("wall.getById", True),
            ("groups.getById", True),
            ("users.get", True),
            ("utils.resolveScreenName", True),
            ("photos.getWallUploadServer", True),
            ("wall.post", False),
            ("wall.delete", False),
            ("wall.repost", False),
            ("photos.saveWallPhoto", False),
            ("messages.send", False),
        ],
    )
    def test_retry_allowlist_covers_the_write_methods_we_actually_call(self, method, safe):
        """Список — allowlist, и цена ошибки в нём несимметрична.

        Забытая запись повторилась бы и задвоила пост; забытое чтение всего
        лишь не получит повтора. Поэтому перечислены безопасные, а не опасные.
        """
        assert _is_retry_safe_method(method) is safe
