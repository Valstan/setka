"""Тесты эндпоинта роли публикации токена (POST /api/tokens/{name}/publish-role).

Сессия БД мокается (``AsyncMock``); используем реальную модель ``VKToken`` —
у неё есть ``to_dict()`` и поле ``role`` (миграция 023).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from database.models import VKToken
from web.api import token_management as tm


def _result_scalar_one(obj):
    r = MagicMock()
    r.scalar_one_or_none.return_value = obj
    return r


def _token(*, name="OLGA", community_id=None, role=None):
    return VKToken(
        id=1,
        name=name,
        token="x" * 30,
        community_id=community_id,
        is_active=True,
        validation_status="unknown",
        role=role,
    )


async def test_enable_publish_role_sets_publish():
    token = _token(role=None)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(token))

    resp = await tm.set_token_publish_role(
        "olga", tm.TokenPublishRoleRequest(enabled=True), db=session
    )

    assert token.role == "publish"
    assert resp.role == "publish"
    session.commit.assert_awaited_once()


async def test_disable_publish_role_clears_to_none():
    token = _token(role="publish")
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(token))

    resp = await tm.set_token_publish_role(
        "olga", tm.TokenPublishRoleRequest(enabled=False), db=session
    )

    assert token.role is None
    assert resp.role is None


async def test_publish_role_404_for_unknown_token():
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(None))

    with pytest.raises(HTTPException) as exc:
        await tm.set_token_publish_role(
            "nope", tm.TokenPublishRoleRequest(enabled=True), db=session
        )
    assert exc.value.status_code == 404
    session.commit.assert_not_awaited()


async def test_publish_role_400_for_community_token():
    token = _token(name="COMM_158", community_id=158)
    session = AsyncMock()
    session.execute = AsyncMock(return_value=_result_scalar_one(token))

    with pytest.raises(HTTPException) as exc:
        await tm.set_token_publish_role(
            "comm_158", tm.TokenPublishRoleRequest(enabled=True), db=session
        )
    assert exc.value.status_code == 400
    session.commit.assert_not_awaited()


def test_to_dict_includes_role():
    assert _token(role="publish").to_dict()["role"] == "publish"
    assert _token(role=None).to_dict()["role"] is None
