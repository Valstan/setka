"""``repair_region``: успех определяется недостающим, а не текстом отчёта.

Ретрай написан ровно для того, чтобы падение попытки не было падением работы.
Пока вызывающий искал в сводке слово «ошибки», регион, у которого аватар встал
со второго захода, записывался в журнал как `error` — и `--repair` докладывал
провалом ровно то, ради чего существует (найдено на живом прогоне 2026-09-01,
`oparino` и `sanchursk`).
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from modules.promotion.group_setup_vk import SetupResult


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "setup_groups_script",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TARGET = {"code": "test", "vk_group_id": -123}


def _run(mod, *, snapshot, avatar_results=(), cover_results=(), community_api=MagicMock()):
    """Прогнать repair_region с заданными снимком и цепочками ответов ВК."""
    avatar_iter = iter(avatar_results)
    cover_iter = iter(cover_results)

    with (
        patch.object(mod, "build_texts", return_value={"avatar": b"a", "cover": b"c"}),
        patch(
            "modules.promotion.group_setup_vk.get_current",
            return_value=SetupResult(ok=True, payload=snapshot),
        ),
        patch(
            "modules.promotion.group_setup_vk.upload_avatar",
            side_effect=lambda *a, **k: next(avatar_iter),
        ),
        patch(
            "modules.promotion.group_setup_vk.upload_cover",
            side_effect=lambda *a, **k: next(cover_iter),
        ),
        patch.object(mod, "interval", return_value=0),
    ):
        return mod.repair_region(TARGET, user_api=MagicMock(), community_api=community_api)


OK = SetupResult(ok=True)
FAIL = SetupResult(ok=False, vk_error_code=129, detail="Invalid photo")


@pytest.fixture(scope="module")
def mod():
    return _load_script()


def test_avatar_landed_on_third_attempt_is_a_success(mod):
    """Два провала и успех — это успех. Прежний код писал сюда error."""
    summary, calls, ok = _run(
        mod,
        snapshot={"has_photo": False, "has_cover": True},
        avatar_results=[FAIL, FAIL, OK],
    )
    assert ok is True
    assert "дозалито: avatar" in summary
    assert "ошибки" in summary, "след ретраев из сводки не выкидываем — по нему видно 129"
    assert calls == 12, "четыре user-вызова на попытку"
    # Гвоздь в прежнее правило: оно читало ту же сводку и говорило «провал».
    # Без этой строки тест зеленел бы и на баге — сводка-то не изменилась.
    assert ("ошибки" not in summary) is False


def test_avatar_never_landed_is_a_failure(mod):
    summary, calls, ok = _run(
        mod,
        snapshot={"has_photo": False, "has_cover": True},
        avatar_results=[FAIL, FAIL, FAIL],
    )
    assert ok is False
    assert "дозалито: ничего" in summary
    assert calls == 12


def test_nothing_missing_costs_nothing(mod):
    summary, calls, ok = _run(mod, snapshot={"has_photo": True, "has_cover": True})
    assert (summary, calls, ok) == ("всё на месте", 0, True)


def test_cover_without_community_key_is_not_silently_ok(mod):
    """Обложки нет и ставить её нечем — это незакрытая цель, а не no-op."""
    summary, calls, ok = _run(
        mod,
        snapshot={"has_photo": True, "has_cover": False},
        community_api=None,
    )
    assert ok is False
    assert "нет community-ключа" in summary
    assert calls == 0, "обложка user-бюджет не тратит"


def test_cover_retry_success_does_not_spend_user_budget(mod):
    summary, calls, ok = _run(
        mod,
        snapshot={"has_photo": True, "has_cover": False},
        cover_results=[FAIL, OK],
    )
    assert ok is True
    assert calls == 0


def test_partial_success_avatar_ok_cover_dead_is_a_failure(mod):
    summary, calls, ok = _run(
        mod,
        snapshot={"has_photo": False, "has_cover": False},
        avatar_results=[OK],
        cover_results=[FAIL, FAIL, FAIL],
    )
    assert ok is False, "закрыта половина целей — работа не сделана"
    assert "avatar" in summary


def test_snapshot_failure_is_not_a_repair(mod):
    """«Не смогли посмотреть» — не «всё починили»: регион обязан вернуться."""
    with patch(
        "modules.promotion.group_setup_vk.get_current",
        return_value=SetupResult(ok=False, detail="timeout"),
    ):
        summary, calls, ok = mod.repair_region(
            TARGET, user_api=MagicMock(), community_api=MagicMock()
        )
    assert ok is False
    assert calls == 0
    assert "снимок не взялся" in summary
