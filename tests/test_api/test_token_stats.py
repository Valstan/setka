"""Тесты счётчиков плашек /tokens.

Чистая функция ``compute_token_stats`` (без БД) + эндпоинт ``GET /api/tokens/stats``
с мок-сессией. Чинит прежнюю заглушку ``main=aux=0`` (устаревший комментарий
«type is not stored in DB» — на деле ``community_id`` есть с миграции 007).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from database.models import VKToken
from web.api import token_management as tm


def _t(community_id=None, validation_status="valid"):
    return {"community_id": community_id, "validation_status": validation_status}


def test_stats_empty_list():
    assert tm.compute_token_stats([]) == {"main": 0, "aux": 0, "broken": 0, "total": 0}


def test_stats_prod_like_mix():
    """2 валидных user + 17 валидных community + 5 невалидных = 24."""
    tokens = (
        [_t(community_id=None) for _ in range(2)]
        + [_t(community_id=1000 + i) for i in range(17)]
        + [_t(community_id=None, validation_status="invalid") for _ in range(3)]
        + [_t(community_id=None, validation_status="unknown") for _ in range(2)]
    )
    assert tm.compute_token_stats(tokens) == {
        "main": 2,
        "aux": 17,
        "broken": 5,
        "total": 24,
    }


def test_stats_partition_invariant():
    tokens = [
        _t(community_id=None),
        _t(community_id=42),
        _t(community_id=None, validation_status="unknown"),
        _t(community_id=99, validation_status="invalid"),
    ]
    s = tm.compute_token_stats(tokens)
    assert s["main"] + s["aux"] + s["broken"] == s["total"]


def test_stats_invalid_community_token_counts_as_broken_not_aux():
    """Невалидный community-токен → broken, НЕ aux."""
    s = tm.compute_token_stats([_t(community_id=7, validation_status="invalid")])
    assert s == {"main": 0, "aux": 0, "broken": 1, "total": 1}


def test_stats_unknown_status_is_broken():
    s = tm.compute_token_stats([_t(community_id=None, validation_status=None)])
    assert s == {"main": 0, "aux": 0, "broken": 1, "total": 1}


async def test_stats_endpoint_with_mocked_session():
    tokens = [
        VKToken(id=1, name="VALSTAN", token="x" * 30, community_id=None, validation_status="valid"),
        VKToken(
            id=2,
            name="COMM_1",
            token="x" * 30,
            community_id=168170001,
            validation_status="valid",
        ),
        VKToken(id=3, name="OLGA", token="x" * 30, community_id=None, validation_status="invalid"),
    ]
    scalars = MagicMock()
    scalars.all.return_value = tokens
    result = MagicMock()
    result.scalars.return_value = scalars
    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)

    out = await tm.get_token_stats(db=session)
    assert out == {"main": 1, "aux": 1, "broken": 1, "total": 3}
