"""Массовая правка сообществ идёт с паузами — и паузы стоят в коде, а не в голове.

06.09 прогон описаний отправил сорок ``groups.edit`` подряд. ВК закрыл канал так,
что перестал срабатывать не только скрипт, но и **ручная правка в браузере**, — и
не на минуту, а на часы. Ошибки при этом не показывалось ни там, ни там: запись
принималась и не происходила.

Соседние режимы того же скрипта (``process_region``, ``repair_region``) тикали
``interval()`` с самого начала; режимы «описания» и «перезалить обложку» — нет.
07.09 хвост дописали, разбив прогон на порции руками, снаружи. Это лечит инстанцию:
правило существует ровно пока его помнит агент, а следующая сессия начинается с
чистой памяти. Тесты ниже — про то, что темп теперь свойство кода.

Пауза меряется по вызовам ``interval()``: он вызывается ровно один раз на паузу,
поэтому счётчик его вызовов и есть число пауз.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from modules.promotion.group_setup_vk import SetupResult


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "setup_groups_script_pacing",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_script()


def _target(code: str, region_id: int):
    return {"code": code, "region_id": region_id, "vk_group_id": -(1000 + region_id)}


async def _refresh(mod, targets, *, current, apply: bool):
    """Прогнать --refresh-desc. ``current`` — код региона → текст, лежащий в ВК.

    Возвращает (число записей в ВК, число пауз).
    """
    by_gid = {abs(t["vk_group_id"]): current[t["code"]] for t in targets}
    writes = []

    async def _targets(_codes, kinds=None):
        return targets

    async def _tokens():
        return "user-token", {abs(t["vk_group_id"]): "comm" for t in targets}

    async def _no_journal_row(_region_id, _version):
        # Журнал к темпу отношения не имеет, а ходит в БД — отрезаем.
        return None

    pause = MagicMock(return_value=0)

    with (
        patch.object(mod, "load_targets", _targets),
        patch.object(mod, "load_tokens", _tokens),
        patch.object(mod, "_journal_row_id", _no_journal_row),
        patch.object(mod, "interval", pause),
        patch.object(mod, "build_texts", lambda t: {"description": "ШАБЛОН"}),
        patch("vk_api.VkApi", MagicMock()),
        patch(
            "modules.promotion.group_setup_vk.get_current",
            side_effect=lambda api, gid: SetupResult(
                ok=True, payload={"description": by_gid[abs(gid)]}
            ),
        ),
        patch(
            "modules.promotion.group_setup_vk.edit_description",
            side_effect=lambda api, gid, text: writes.append(gid) or SetupResult(ok=True),
        ),
    ):
        await mod.run_refresh_desc(None, apply=apply)

    return len(writes), pause.call_count


class TestRefreshDescPacing:
    @pytest.mark.asyncio
    async def test_pause_stands_between_every_pair_of_writes(self, mod):
        """Три записи — две паузы. Гвоздь: до починки пауз было ноль."""
        targets = [_target("uni", 1), _target("svecha", 2), _target("zuevka", 3)]
        written, pauses = await _refresh(
            mod,
            targets,
            current={"uni": "старое", "svecha": "старое", "zuevka": "старое"},
            apply=True,
        )
        assert written == 3
        assert pauses == 2

    @pytest.mark.asyncio
    async def test_single_write_waits_for_nothing(self, mod):
        """Одна группа — ни одной паузы: ждать не за чем и не перед чем."""
        written, pauses = await _refresh(
            mod, [_target("mi", 1)], current={"mi": "старое"}, apply=True
        )
        assert (written, pauses) == (1, 0)

    @pytest.mark.asyncio
    async def test_skipped_communities_are_not_paid_for_with_waiting(self, mod):
        """Пропуск квоту не тратит — значит и паузы не стоит.

        Не косметика: на срезе 07.09 из 43 сообществ 34 были уже шаблонными.
        Пауза «на каждом обороте цикла» превратила бы холостой проход в три
        минуты сна и подтолкнула бы следующего оператора отключить темп совсем.
        """
        targets = [_target("uni", 1), _target("svecha", 2), _target("zuevka", 3)]
        written, pauses = await _refresh(
            mod,
            targets,
            current={"uni": "ШАБЛОН", "svecha": "ШАБЛОН", "zuevka": "старое"},
            apply=True,
        )
        assert written == 1, "две группы уже шаблонные — запись идёт только третьей"
        assert pauses == 0, "паузу оплачивает запись, а не обход"

    @pytest.mark.asyncio
    async def test_dry_run_does_not_sleep(self, mod):
        """Сухой прогон не пишет в ВК — значит и темп ему не нужен."""
        targets = [_target("uni", 1), _target("svecha", 2)]
        written, pauses = await _refresh(
            mod, targets, current={"uni": "старое", "svecha": "старое"}, apply=False
        )
        assert (written, pauses) == (0, 0)


class TestForceCoverPacing:
    @pytest.mark.asyncio
    async def test_cover_reupload_is_paced_too(self, mod):
        """--force-cover ходит по всей сети тем же аккаунтом и той же квотой."""
        targets = [_target("uni", 1), _target("svecha", 2), _target("zuevka", 3)]
        pause = MagicMock(return_value=0)
        uploads = []

        async def _targets(codes):
            return targets

        async def _tokens():
            return "user-token", {abs(t["vk_group_id"]): "comm" for t in targets}

        async def _ours():
            return {t["region_id"] for t in targets}

        with (
            patch.object(mod, "load_targets", _targets),
            patch.object(mod, "load_tokens", _tokens),
            patch.object(mod, "interval", pause),
            patch.object(mod, "build_texts", lambda t: {"cover": b"png"}),
            patch.object(mod, "neighbor_index", lambda t: {}),
            patch("vk_api.VkApi", MagicMock()),
            patch(
                "modules.promotion.group_setup_vk.upload_cover",
                side_effect=lambda api, gid, cover: uploads.append(gid) or SetupResult(ok=True),
            ),
            patch.object(mod, "_regions_we_dressed", _ours),
        ):
            await mod.run_force_cover(None)

        assert len(uploads) == 3
        assert pause.call_count == 2
