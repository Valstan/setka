"""Тесты D-047: привязка ключей шлюза к owner_id (modules/gateway/scope.py).

Ядро мандата brain 2026-08-25: у каждого ключа список разрешённых owner_id;
вызов, чья цель не в списке, — 403. Ключ без привязки — отказ по owner-scoped
методам. Экстракция целей — по каждому методу allowlist'а отдельно, незнакомый
метод = отказ (fail-closed: гейт, который не умеет краснеть, ничего не охраняет).
"""

import pytest

from config.gateway import GATEWAY_READ_METHODS
from modules.gateway.scope import GLOBAL_METHODS, KeyBinding, check_method_scope, extract_targets

BINDING = KeyBinding.from_lists(
    owner_ids=[-218991929, -195583920, 86086407],
    screen_names=["kalinino_sdk", "rmz43"],
)
UNBOUND = None


# --- полнота карты: каждый метод allowlist'а обязан иметь правило -----------
def test_extraction_map_covers_entire_allowlist():
    """Метод, добавленный в allowlist без правила экстракции, не должен молча
    проскакивать enforcement — проверяем полноту на импорте, а не на проде."""
    from modules.gateway.scope import _EXTRACTORS

    covered = set(_EXTRACTORS) | set(GLOBAL_METHODS)
    assert covered >= set(GATEWAY_READ_METHODS), (
        "методы без правила экстракции: " f"{sorted(set(GATEWAY_READ_METHODS) - covered)}"
    )


def test_unknown_method_fails_closed():
    refusal = check_method_scope("audio.get", {"owner_id": -218991929}, BINDING)
    assert refusal is not None


# --- wall.get: owner_id / domain / умолчание -------------------------------
def test_wall_get_allowed_owner_int():
    assert check_method_scope("wall.get", {"owner_id": -218991929}, BINDING) is None


def test_wall_get_allowed_owner_str():
    """JSON-body может принести owner_id строкой — нормализуем."""
    assert check_method_scope("wall.get", {"owner_id": "-218991929"}, BINDING) is None


def test_wall_get_foreign_owner_refused():
    assert check_method_scope("wall.get", {"owner_id": -24611937}, BINDING) is not None


def test_wall_get_domain_screen_name():
    assert check_method_scope("wall.get", {"domain": "rmz43"}, BINDING) is None
    assert check_method_scope("wall.get", {"domain": "apiclub"}, BINDING) is not None


def test_wall_get_domain_case_insensitive():
    assert check_method_scope("wall.get", {"domain": "RMZ43"}, BINDING) is None


def test_wall_get_no_target_refused():
    """Без owner_id/domain VK подставил бы владельца НАШЕГО токена — отказ."""
    assert check_method_scope("wall.get", {"count": 5}, BINDING) is not None


# --- wall.getById: posts CSV "owner_postid" --------------------------------
def test_wall_get_by_id_posts_parsed():
    ok = {"posts": "-218991929_109167,-195583920_5327"}
    assert check_method_scope("wall.getById", ok, BINDING) is None


def test_wall_get_by_id_foreign_post_refused():
    mixed = {"posts": "-218991929_1,-24611937_2"}
    assert check_method_scope("wall.getById", mixed, BINDING) is not None


def test_wall_get_by_id_garbage_refused():
    assert check_method_scope("wall.getById", {"posts": "notapost"}, BINDING) is not None
    assert check_method_scope("wall.getById", {}, BINDING) is not None


# --- groups.*: положительный id группы == owner -id ------------------------
def test_groups_get_by_id_numeric_and_names():
    assert check_method_scope("groups.getById", {"group_ids": "218991929"}, BINDING) is None
    assert check_method_scope("groups.getById", {"group_ids": "kalinino_sdk"}, BINDING) is None
    assert check_method_scope("groups.getById", {"group_ids": "apiclub"}, BINDING) is not None


def test_groups_get_by_id_csv_all_must_be_allowed():
    mixed = {"group_ids": "218991929,24611937"}
    assert check_method_scope("groups.getById", mixed, BINDING) is not None


def test_groups_get_by_id_list_input():
    """Потребитель может прислать список вместо CSV — VKClient его склеит."""
    assert check_method_scope("groups.getById", {"group_ids": [218991929]}, BINDING) is None


def test_groups_get_by_id_group_id_fallback_and_absent():
    assert check_method_scope("groups.getById", {"group_id": "218991929"}, BINDING) is None
    assert check_method_scope("groups.getById", {}, BINDING) is not None


def test_groups_members_and_is_member():
    assert check_method_scope("groups.getMembers", {"group_id": 218991929}, BINDING) is None
    assert check_method_scope("groups.getMembers", {"group_id": 24611937}, BINDING) is not None
    assert check_method_scope("groups.isMember", {"group_id": "kalinino_sdk"}, BINDING) is None
    assert check_method_scope("groups.isMember", {}, BINDING) is not None


def test_board_topics_group_id():
    assert check_method_scope("board.getTopics", {"group_id": 218991929}, BINDING) is None
    assert check_method_scope("board.getComments", {"group_id": 24611937}, BINDING) is not None


# --- users.*: положительные id, умолчание = владелец токена → отказ --------
def test_users_get_ids():
    assert check_method_scope("users.get", {"user_ids": "86086407"}, BINDING) is None
    assert check_method_scope("users.get", {"user_ids": "1"}, BINDING) is not None
    assert check_method_scope("users.get", {}, BINDING) is not None


def test_users_get_csv_mixed_refused():
    assert check_method_scope("users.get", {"user_ids": "86086407,1"}, BINDING) is not None


def test_users_followers_and_subscriptions():
    assert check_method_scope("users.getFollowers", {"user_id": 86086407}, BINDING) is None
    assert check_method_scope("users.getFollowers", {}, BINDING) is not None
    assert check_method_scope("users.getSubscriptions", {"user_id": 1}, BINDING) is not None


# --- photos/video/likes/stats: owner_id обязателен -------------------------
@pytest.mark.parametrize(
    "method",
    [
        "photos.get",
        "photos.getAlbums",
        "wall.getComments",
        "wall.getReposts",
        "likes.getList",
        "stats.getPostReach",
    ],
)
def test_owner_scoped_methods_require_owner(method):
    assert check_method_scope(method, {"owner_id": -218991929}, BINDING) is None
    assert check_method_scope(method, {"owner_id": -24611937}, BINDING) is not None
    assert check_method_scope(method, {}, BINDING) is not None


def test_video_get_videos_csv_owners_checked():
    assert check_method_scope("video.get", {"owner_id": -218991929}, BINDING) is None
    assert check_method_scope("video.get", {}, BINDING) is not None
    mixed = {"owner_id": -218991929, "videos": "-24611937_456239017"}
    assert check_method_scope("video.get", mixed, BINDING) is not None


def test_video_get_videos_only_live_pattern():
    """Живой паттерн CDK_KALININO: только videos, без owner_id (замер 26.08)."""
    own = {"videos": "-218991929_456239102,-218991929_456239103"}
    assert check_method_scope("video.get", own, BINDING) is None
    foreign = {"videos": "-218991929_1,-24611937_2"}
    assert check_method_scope("video.get", foreign, BINDING) is not None
    assert check_method_scope("video.get", {"videos": "garbage"}, BINDING) is not None


# --- dual-alias smuggling: оба идентифицирующих параметра сразу ------------
# Параметры уходят в VK как есть, и чей приоритет у VK — недокументировано.
# Значит проверять надо ВСЕ присутствующие, а не первый попавшийся
# (блокер adversarial-ревью 2026-08-26).
def test_wall_get_both_owner_and_domain_checks_both():
    smuggle = {"owner_id": -218991929, "domain": "apiclub"}
    assert check_method_scope("wall.get", smuggle, BINDING) is not None
    both_ok = {"owner_id": -218991929, "domain": "rmz43"}
    assert check_method_scope("wall.get", both_ok, BINDING) is None


def test_groups_get_by_id_both_aliases_check_both():
    smuggle = {"group_ids": "218991929", "group_id": "24611937"}
    assert check_method_scope("groups.getById", smuggle, BINDING) is not None
    both_ok = {"group_ids": "218991929", "group_id": "195583920"}
    assert check_method_scope("groups.getById", both_ok, BINDING) is None


# --- пустая цель: present-but-empty == absent (защита от vacuous pass) -----
@pytest.mark.parametrize("empty", ["", ",", " , "])
def test_users_get_present_but_empty_refused(empty):
    assert check_method_scope("users.get", {"user_ids": empty}, BINDING) is not None


def test_groups_get_by_id_empty_list_refused():
    assert check_method_scope("groups.getById", {"group_ids": []}, BINDING) is not None


# --- GLOBAL_METHODS приколочен гвоздями ------------------------------------
def test_global_methods_pinned_exactly():
    """Ленивый зелёный путь для нового owner-scoped метода — кинуть его в
    GLOBAL_METHODS. Этот тест делает такой ход осознанным решением, а не
    случайностью: меняешь список — меняешь и тест, глядя в глаза ревьюеру."""
    assert GLOBAL_METHODS == frozenset(
        {
            "groups.search",
            "newsfeed.search",
            "database.getCities",
            "database.getCountries",
            "utils.resolveScreenName",
        }
    )
    from modules.gateway.scope import _EXTRACTORS

    assert not (set(_EXTRACTORS) & GLOBAL_METHODS)


# --- глобальные методы: без цели, разрешены аутентифицированному ключу -----
@pytest.mark.parametrize("method", sorted(GLOBAL_METHODS))
def test_global_methods_allowed_even_unbound(method):
    assert check_method_scope(method, {"q": "малмыж"}, BINDING) is None
    assert check_method_scope(method, {"q": "малмыж"}, UNBOUND) is None


# --- непривязанный ключ: отказ по owner-scoped -----------------------------
def test_unbound_key_refused_for_owner_scoped():
    assert check_method_scope("wall.get", {"owner_id": -218991929}, UNBOUND) is not None


def test_empty_binding_is_unbound():
    empty = KeyBinding.from_lists(owner_ids=[], screen_names=[])
    assert check_method_scope("wall.get", {"owner_id": -218991929}, empty) is not None


# --- нормализация и мусор ---------------------------------------------------
def test_whitespace_in_csv_tolerated():
    assert (
        check_method_scope("groups.getById", {"group_ids": " 218991929 , kalinino_sdk "}, BINDING)
        is None
    )


def test_garbage_owner_refused():
    assert check_method_scope("wall.get", {"owner_id": "abc"}, BINDING) is not None
    assert check_method_scope("wall.get", {"owner_id": None}, BINDING) is not None
    assert check_method_scope("wall.get", {"owner_id": [1, 2]}, BINDING) is not None


def test_float_owner_refused():
    """float молча превратился бы в int и обошёл бы сравнение строк — отказ."""
    assert check_method_scope("wall.get", {"owner_id": -218991929.0}, BINDING) is not None


# --- load_binding: БД-половина гейта ----------------------------------------
def _binding_row(ids, names):
    from types import SimpleNamespace

    return SimpleNamespace(allowed_owner_ids=ids, allowed_screen_names=names)


def _db_returning_row(row):
    from contextlib import asynccontextmanager
    from unittest.mock import AsyncMock, MagicMock

    session = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    session.execute = AsyncMock(return_value=result)

    @asynccontextmanager
    async def _factory():
        yield session

    return _factory


@pytest.mark.asyncio
async def test_load_binding_reads_row():
    from unittest.mock import patch

    from modules.gateway.scope import load_binding

    row = _binding_row([-195583920], ["rmz43"])
    with patch("database.connection.AsyncSessionLocal", _db_returning_row(row)):
        b = await load_binding("RMZ")
    assert b is not None and -195583920 in b.owner_ids and "rmz43" in b.screen_names


@pytest.mark.asyncio
async def test_load_binding_null_columns_is_unbound():
    from unittest.mock import patch

    from modules.gateway.scope import load_binding

    with patch(
        "database.connection.AsyncSessionLocal", _db_returning_row(_binding_row(None, None))
    ):
        b = await load_binding("OLD")
    assert b is not None and not b.is_bound


@pytest.mark.asyncio
async def test_load_binding_no_row_is_none():
    from unittest.mock import patch

    from modules.gateway.scope import load_binding

    with patch("database.connection.AsyncSessionLocal", _db_returning_row(None)):
        assert await load_binding("GHOST") is None


@pytest.mark.asyncio
async def test_load_binding_db_error_raises_not_unbound():
    """Недоступная БД — не «не привязан»: наружу другой текст отказа."""
    from unittest.mock import patch

    from modules.gateway.scope import BindingLoadError, check_call_scope, load_binding

    def _boom():
        raise RuntimeError("db down")

    with patch("database.connection.AsyncSessionLocal", _boom):
        with pytest.raises(BindingLoadError):
            await load_binding("RMZ")
        refusal = await check_call_scope("RMZ", "wall.get", {"owner_id": -195583920})
    assert refusal is not None and "temporarily" in refusal
    assert "no owner binding" not in refusal


@pytest.mark.asyncio
async def test_load_binding_malformed_shape_is_unbound():
    """Рукотворный UPDATE мимо валидации (скаляр вместо списка) — не привязан,
    а не итерация строки посимвольно."""
    from unittest.mock import patch

    from modules.gateway.scope import load_binding

    row = _binding_row("kalinino_sdk", None)
    with patch("database.connection.AsyncSessionLocal", _db_returning_row(row)):
        b = await load_binding("CDK_KALININO")
    assert b is None  # None == «не привязан» для check_method_scope


# --- extract_targets: прямые проверки экстрактора ---------------------------
def test_extract_targets_wall_get():
    assert extract_targets("wall.get", {"owner_id": -195583920}) == [-195583920]
    assert extract_targets("wall.get", {"domain": "RMZ43"}) == ["rmz43"]


def test_binding_from_lists_normalizes():
    b = KeyBinding.from_lists(owner_ids=["-195583920", 86086407], screen_names=["RMZ43"])
    assert -195583920 in b.owner_ids
    assert 86086407 in b.owner_ids
    assert "rmz43" in b.screen_names
