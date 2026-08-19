"""Подпись владельца ключа и сверка «ключ ↔ сообщество» (заказ владельца 2026-08-19).

Две вещи, которых на странице `/tokens` не было:

1. **Кому принадлежит ключ, человеческим языком.** Имя `COMM_240944863`
   отвечает «какая группа», но не «какой район» — соответствие оператор держал
   в голове либо искал в другой таблице той же страницы.
2. **Настоящая проверка принадлежности.** Прежняя валидация звала
   `groups.getById(group_id=<cid>)` и считала успех доказательством. Это не
   доказательство: карточка публичной группы читается любым токеном, поэтому
   ключ, вставленный не в ту строку, показывался «ВАЛИДЕН».
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from web.api import token_management as tm


class _Region:
    def __init__(self, code, name):
        self.code = code
        self.name = name


def test_community_token_is_labelled_by_its_district():
    out = tm.describe_subject(
        community_id=240944863,
        user_info=None,
        region=_Region("yurya", "ЮРЬЯ - ИНФО"),
    )
    assert out["subject_kind"] == "community"
    assert out["subject_label"] == "ЮРЬЯ - ИНФО"
    assert out["subject_url"] == "https://vk.com/club240944863"
    assert out["region_code"] == "yurya"


def test_community_without_region_falls_back_to_group_id():
    """Осиротевший ключ не должен остаться безымянным — по нему ещё принимать
    решение, удалять или нет (25.07 удалили рабочий ключ Радара, посчитав мусором)."""
    out = tm.describe_subject(community_id=137760500, user_info=None, region=None)
    assert out["subject_label"] == "club137760500"
    assert out["subject_url"] == "https://vk.com/club137760500"
    assert out["region_code"] is None


def test_negative_group_id_is_normalised():
    """В базе `vk_group_id` отрицательный, а в ссылке нужен модуль."""
    out = tm.describe_subject(community_id=-179203620, user_info=None, region=None)
    assert out["subject_url"] == "https://vk.com/club179203620"


def test_user_token_is_labelled_by_its_owner():
    out = tm.describe_subject(
        community_id=None,
        user_info={"id": 20002978, "first_name": "Валентин", "last_name": "Савиных"},
    )
    assert out["subject_kind"] == "user"
    assert out["subject_label"] == "Валентин Савиных"
    assert out["subject_url"] == "https://vk.com/id20002978"


def test_user_token_without_snapshot_is_unknown_not_crash():
    out = tm.describe_subject(community_id=None, user_info=None)
    assert out["subject_kind"] == "unknown"
    assert out["subject_label"] is None
    assert out["subject_url"] is None


# ─── проверка принадлежности ────────────────────────────────────────────────


def _api_returning(own_group):
    """vk_api-подобный объект, чей `groups.getById()` отдаёт свою группу."""
    api = MagicMock()
    api.groups.getById.return_value = [own_group]
    api.messages.getConversations.return_value = {"items": []}
    api.wall.get.return_value = {"items": []}
    return api


@pytest.mark.asyncio
async def test_matching_token_is_valid(monkeypatch):
    api = _api_returning({"id": 240944863, "name": "ЮРЬЯ - ИНФО"})
    monkeypatch.setattr(
        "vk_api.VkApi", lambda token: MagicMock(get_api=MagicMock(return_value=api))
    )
    out = await tm.validate_community_token("t", 240944863)
    assert out["is_valid"] is True
    assert out["owns_community"] is True
    # Спрашиваем токен о НЁМ САМОМ — без аргументов.
    api.groups.getById.assert_called_once_with()


@pytest.mark.asyncio
async def test_swapped_token_is_caught(monkeypatch):
    """Главный регресс: ключ Слободского, записанный в строку Юрьи.

    На прежней проверке этот случай проходил как «ВАЛИДЕН».
    """
    api = _api_returning({"id": 240945121, "name": "СЛОБОДСКОЙ - ИНФО"})
    monkeypatch.setattr(
        "vk_api.VkApi", lambda token: MagicMock(get_api=MagicMock(return_value=api))
    )
    out = await tm.validate_community_token("t", 240944863)
    assert out["is_valid"] is False
    assert out["owns_community"] is False
    assert "240945121" in out["error_message"]
    assert "СЛОБОДСКОЙ - ИНФО" in out["error_message"]
    # Дальше по правам не идём: смысла проверять чужой ключ нет.
    api.messages.getConversations.assert_not_called()


@pytest.mark.asyncio
async def test_token_that_does_not_name_its_group_is_not_valid(monkeypatch):
    api = MagicMock()
    api.groups.getById.return_value = []
    monkeypatch.setattr(
        "vk_api.VkApi", lambda token: MagicMock(get_api=MagicMock(return_value=api))
    )
    out = await tm.validate_community_token("t", 240944863)
    assert out["is_valid"] is False
    assert out["owns_community"] is None
