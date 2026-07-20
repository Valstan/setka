"""Validation helpers for redundant per-community token slots."""

import pytest
from fastapi import HTTPException

from web.api.token_management import _validate_community_token_name


def test_accepts_legacy_and_named_community_tokens():
    assert _validate_community_token_name(158, "comm_158") == "COMM_158"
    assert _validate_community_token_name(-158, "comm_158_mama") == "COMM_158_MAMA"


def test_rejects_token_name_for_another_community():
    with pytest.raises(HTTPException) as exc:
        _validate_community_token_name(158, "COMM_999_MAMA")
    assert exc.value.status_code == 400
