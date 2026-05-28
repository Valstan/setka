"""Tests for :class:`modules.vk_token_router.TokenPolicy`.

Покрывают:
- pick(READ): возвращает все active user-токены, включая Vita.
- pick(COMMUNITY_WRITE): community-токен первым, потом whitelist минус Vita.
- pick(USER_WRITE): только whitelist минус Vita.
- Vita НЕ попадает в WRITE-кандидаты ни при каких условиях.
- Valstan в cooldown (disabled_until > now) → выпадает из всех op'ов.
- report_error(name, 5) → disabled_until = now + 24h; (29) → 1h; (100) → только consecutive++
- report_success(name) → consecutive_errors = 0.
- disable(name, hours) / enable(name) — manual control.

Сессия мокается через ``AsyncMock`` (см. tests/conftest.py — паттерн mock_db_session).
"""

import os
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Гарантируем env до импорта rt-модулей (общий паттерн conftest, но локально тоже).
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")

from database.models import VKToken  # noqa: E402
from modules.vk_token_router import TokenOp, TokenPolicy  # noqa: E402


def _vk_token_row(
    name: str,
    token_value: str,
    *,
    community_id=None,
    is_active=True,
    disabled_until=None,
    consecutive_errors=0,
    validation_status=None,
):
    """Лёгкий builder реальной модели VKToken (не Mock — модель ловит атрибуты).

    Используем настоящий ``VKToken`` чтобы код TokenPolicy получал атрибуты как
    в проде; mockSession.execute возвращает iterable scalars().
    """
    return VKToken(
        id=1,
        name=name,
        token=token_value,
        community_id=community_id,
        is_active=is_active,
        disabled_until=disabled_until,
        consecutive_errors=consecutive_errors,
        validation_status=validation_status,
    )


class _ScalarsResult:
    """Минимальная замена .scalars() — итерабельна и поддерживает .all()."""

    def __init__(self, rows):
        self._rows = list(rows)

    def __iter__(self):
        return iter(self._rows)

    def all(self):
        return list(self._rows)


def _make_session_with_rows(rows_by_query=None, single_row=None):
    """Async session-mock с queue execute-результатов.

    ``rows_by_query`` — список iterables, в порядке вызовов execute(). Каждый
    очередной execute(...) возвращает результат с .scalars() == следующий.
    ``single_row`` — для запросов scalar_one_or_none.
    """
    session = AsyncMock()

    queue = list(rows_by_query or [])

    async def _execute(*a, **kw):
        result = MagicMock()
        if queue:
            rows = queue.pop(0)
            result.scalars.return_value = _ScalarsResult(rows)
        else:
            result.scalars.return_value = _ScalarsResult([])
        result.scalar_one_or_none.return_value = single_row
        result.rowcount = 1
        return result

    session.execute.side_effect = _execute
    session.commit = AsyncMock()
    return session


# ---------------------------------------------------------------------------
# pick(): семантика операций
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pick_read_includes_vita():
    """READ — любой active user-token, включая Vita."""
    rows_active = [
        _vk_token_row("VALSTAN", "tok_v"),
        _vk_token_row("VITA", "tok_vita"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows_active])
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": os.environ["REDIS_URL"],
            "VK_TOKEN_VALSTAN": "tok_v",
            "VK_TOKEN_VITA": "tok_vita",
        },
        clear=True,
    ):
        import importlib

        import config.runtime as rt

        importlib.reload(rt)
        policy = TokenPolicy(session)
        out = await policy.pick(TokenOp.READ)
    names = [c.name for c in out]
    assert "VALSTAN" in names and "VITA" in names
    assert all(c.source == "user" for c in out)


@pytest.mark.asyncio
async def test_pick_user_write_excludes_vita():
    """USER_WRITE — только whitelist минус Vita."""
    rows_active = [
        _vk_token_row("VALSTAN", "tok_v"),
        _vk_token_row("VITA", "tok_vita"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows_active])
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": os.environ["REDIS_URL"],
            "VK_TOKEN_VALSTAN": "tok_v",
            "VK_TOKEN_VITA": "tok_vita",
            "VK_PUBLISH_TOKEN_NAMES": "VALSTAN,VITA",  # даже если кто-то поставит VITA
            "VK_NEVER_PUBLISH_TOKEN_NAMES": "VITA",
        },
        clear=True,
    ):
        import importlib

        import config.runtime as rt

        importlib.reload(rt)
        policy = TokenPolicy(session)
        out = await policy.pick(TokenOp.USER_WRITE)
    names = [c.name for c in out]
    assert names == ["VALSTAN"]
    assert all(c.source == "user" for c in out)


@pytest.mark.asyncio
async def test_pick_community_write_prefers_community_token():
    """COMMUNITY_WRITE: для group_id=158 community-токен идёт первым."""
    user_rows = [_vk_token_row("VALSTAN", "tok_v")]
    comm_rows = [_vk_token_row("COMM_158", "tok_comm", community_id=158)]
    # Порядок execute(): active user → communities (внутри pick'а).
    session = _make_session_with_rows(rows_by_query=[user_rows, comm_rows])
    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": os.environ["REDIS_URL"],
            "VK_TOKEN_VALSTAN": "tok_v",
            "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
        },
        clear=True,
    ):
        import importlib

        import config.runtime as rt

        importlib.reload(rt)
        policy = TokenPolicy(session)
        out = await policy.pick(TokenOp.COMMUNITY_WRITE, group_id=-158)
    assert len(out) == 2
    assert out[0].source == "community"
    assert out[0].community_id == 158
    assert out[1].name == "VALSTAN"
    assert out[1].source == "user"


@pytest.mark.asyncio
async def test_pick_skips_valstan_in_cooldown():
    """Valstan с disabled_until > now → не в pick(READ) и не в pick(USER_WRITE)."""
    future = datetime.utcnow() + timedelta(hours=10)
    rows_active = [
        # disabled_until > now — _load_active отбросит запись Valstan.
        # Помещаем только Vita в active, имитируя что _load_active фильтрует.
        _vk_token_row("VITA", "tok_vita"),
    ]
    # Для _token_exists_but_disabled — отдельный execute с одним row Valstan.
    valstan_disabled = _vk_token_row("VALSTAN", "tok_v", disabled_until=future, is_active=True)

    session = AsyncMock()
    call_count = {"n": 0}

    async def _execute(*a, **kw):
        call_count["n"] += 1
        result = MagicMock()
        # 1-й вызов — _load_active (только Vita)
        # 2-й вызов (если будет) — _token_exists_but_disabled на VALSTAN
        if call_count["n"] == 1:
            result.scalars.return_value = _ScalarsResult(rows_active)
        else:
            result.scalars.return_value = _ScalarsResult([])
            result.scalar_one_or_none.return_value = valstan_disabled
        return result

    session.execute.side_effect = _execute
    session.commit = AsyncMock()

    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": os.environ["REDIS_URL"],
            "VK_TOKEN_VALSTAN": "tok_v",
            "VK_TOKEN_VITA": "tok_vita",
            "VK_PUBLISH_TOKEN_NAMES": "VALSTAN",
        },
        clear=True,
    ):
        import importlib

        import config.runtime as rt

        importlib.reload(rt)
        policy = TokenPolicy(session)

        read_out = await policy.pick(TokenOp.READ)
        read_names = [c.name for c in read_out]
        assert "VITA" in read_names
        assert "VALSTAN" not in read_names

        write_out = await policy.pick(TokenOp.USER_WRITE)
        # Vita запрещена, Valstan в cooldown → пусто
        assert write_out == []


# ---------------------------------------------------------------------------
# report_error / report_success
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_error_5_sets_24h_cooldown():
    row = _vk_token_row("VALSTAN", "tok_v")
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    result.scalars.return_value = _ScalarsResult([])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    with patch("modules.vk_token_router._send_telegram_alert_safe", new_callable=AsyncMock):
        policy = TokenPolicy(session)
        before = datetime.utcnow()
        await policy.report_error("VALSTAN", 5)

    assert row.last_error_code == 5
    assert row.disabled_until is not None
    delta = row.disabled_until - before
    assert timedelta(hours=23, minutes=58) < delta < timedelta(hours=24, minutes=2)
    session.commit.assert_awaited()


@pytest.mark.asyncio
async def test_report_error_29_sets_1h_cooldown():
    row = _vk_token_row("VALSTAN", "tok_v")
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    result.scalars.return_value = _ScalarsResult([])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    with patch("modules.vk_token_router._send_telegram_alert_safe", new_callable=AsyncMock):
        policy = TokenPolicy(session)
        before = datetime.utcnow()
        await policy.report_error("VALSTAN", 29)

    assert row.disabled_until is not None
    delta = row.disabled_until - before
    assert timedelta(minutes=58) < delta < timedelta(minutes=62)


@pytest.mark.asyncio
async def test_report_error_other_code_only_counts():
    """Код 100 не в auto-disable списке — только consecutive++ без cooldown."""
    row = _vk_token_row("VALSTAN", "tok_v")
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    result.scalars.return_value = _ScalarsResult([])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    policy = TokenPolicy(session)
    await policy.report_error("VALSTAN", 100)

    assert row.disabled_until is None
    assert row.consecutive_errors == 1


@pytest.mark.asyncio
async def test_report_success_resets_consecutive():
    """report_success — UPDATE с consecutive_errors=0."""
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    policy = TokenPolicy(session)
    await policy.report_success("VALSTAN")
    # Был один UPDATE-вызов.
    session.execute.assert_awaited()
    session.commit.assert_awaited()


# ---------------------------------------------------------------------------
# Manual disable / enable
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_disable_creates_row_if_missing():
    """Если записи в БД ещё нет — disable создаёт новую из env-токена."""
    session = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    result.scalars.return_value = _ScalarsResult([])
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()

    captured = {}

    def _add(obj):
        captured["row"] = obj

    session.add = MagicMock(side_effect=_add)

    with patch.dict(
        os.environ,
        {
            "DATABASE_URL": os.environ["DATABASE_URL"],
            "REDIS_URL": os.environ["REDIS_URL"],
            "VK_TOKEN_VALSTAN": "tok_v",
        },
        clear=True,
    ):
        import importlib

        import config.runtime as rt

        importlib.reload(rt)
        policy = TokenPolicy(session)
        ok = await policy.disable("VALSTAN", hours=24, reason="prod ban 24h")

    assert ok is True
    new_row = captured.get("row")
    assert new_row is not None
    assert new_row.name == "VALSTAN"
    assert new_row.disabled_until is not None
    assert "prod ban" in (new_row.error_message or "")


@pytest.mark.asyncio
async def test_enable_returns_false_if_no_row():
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 0
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    policy = TokenPolicy(session)
    ok = await policy.enable("NONEXISTENT")
    assert ok is False


# ---------------------------------------------------------------------------
# get_active_parse_tokens: значения берутся из БД (single source of truth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_active_parse_tokens_returns_db_values():
    """Возвращает {name: token} из БД для active user-токенов."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [
        _vk_token_row("VALSTAN", "db_tok_valstan", validation_status="valid"),
        _vk_token_row("VITA", "db_tok_vita", validation_status="valid"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VALSTAN": "db_tok_valstan", "VITA": "db_tok_vita"}


@pytest.mark.asyncio
async def test_active_parse_tokens_value_from_db_not_env():
    """Значение токена — из БД, даже если в env лежит другое (рассинхрон)."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [_vk_token_row("VALSTAN", "fresh_db_token", validation_status="valid")]
    session = _make_session_with_rows(rows_by_query=[rows])
    with patch.dict(os.environ, {"VK_TOKEN_VALSTAN": "stale_env_token"}, clear=False):
        out = await get_active_parse_tokens(session)
    assert out == {"VALSTAN": "fresh_db_token"}


@pytest.mark.asyncio
async def test_active_parse_tokens_skips_invalid():
    """validation_status='invalid' — токен в парсинг не берётся."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [
        _vk_token_row("VALSTAN", "tok_valstan", validation_status="invalid"),
        _vk_token_row("VITA", "tok_vita", validation_status="valid"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VITA": "tok_vita"}


@pytest.mark.asyncio
async def test_active_parse_tokens_keeps_unknown():
    """validation_status='unknown'/None — годится (свежедобавленный токен)."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [
        _vk_token_row("VALSTAN", "tok_valstan", validation_status="unknown"),
        _vk_token_row("VITA", "tok_vita", validation_status=None),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VALSTAN": "tok_valstan", "VITA": "tok_vita"}


@pytest.mark.asyncio
async def test_active_parse_tokens_skips_inactive():
    """is_active=False — исключён."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [
        _vk_token_row("VALSTAN", "tok_valstan", is_active=False, validation_status="valid"),
        _vk_token_row("VITA", "tok_vita", validation_status="valid"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VITA": "tok_vita"}


@pytest.mark.asyncio
async def test_active_parse_tokens_skips_cooldown():
    """disabled_until > now() — на cooldown, исключён."""
    from modules.vk_token_router import get_active_parse_tokens

    future = datetime.utcnow() + timedelta(hours=2)
    rows = [
        _vk_token_row("VALSTAN", "tok_valstan", disabled_until=future, validation_status="valid"),
        _vk_token_row("VITA", "tok_vita", validation_status="valid"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VITA": "tok_vita"}


@pytest.mark.asyncio
async def test_active_parse_tokens_skips_empty_token():
    """Пустой token в БД — исключён (плейсхолдер-строки)."""
    from modules.vk_token_router import get_active_parse_tokens

    rows = [
        _vk_token_row("ELIS", "", validation_status="unknown"),
        _vk_token_row("VITA", "tok_vita", validation_status="valid"),
    ]
    session = _make_session_with_rows(rows_by_query=[rows])
    out = await get_active_parse_tokens(session)
    assert out == {"VITA": "tok_vita"}
