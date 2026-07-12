"""dry_run contract for parse_and_publish_theme (feat/bulletin-dry-run).

Гарантия: ``dry_run=True`` прогоняет парсинг → фильтр → сборку сводки, но
НЕ публикует (VKPublisher не дёргается), НЕ коммитит в БД и возвращает
``would_publish`` с превью. Используется страницей /regions/<code>/diagnostics.

Пайплайн тяжёлый — мокаем коллабораторов в их исходных модулях (внутри
``_execute`` они импортируются по месту, поэтому патч исходного модуля работает).
"""

from __future__ import annotations

import asyncio
import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from modules.vk_token_router import TokenCandidate


def _res(first=None, sfirst=None, sall=None, fall=None):
    r = MagicMock()
    r.first.return_value = first
    r.scalars.return_value.first.return_value = sfirst
    r.scalars.return_value.all.return_value = sall or []
    r.fetchall.return_value = fall or []
    return r


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.commit = AsyncMock()
        self.add = MagicMock()
        self.refresh = AsyncMock()

    async def execute(self, stmt):
        return self._results.pop(0)


class _FakeSessionCM:
    def __init__(self, results):
        self._results = results

    async def __aenter__(self):
        self.session = _FakeSession(self._results)
        return self.session

    async def __aexit__(self, *exc):
        return False


def test_dry_run_builds_preview_without_publish_or_commit():
    from tasks import parsing_scheduler_tasks as st

    region_ns = SimpleNamespace(
        id=1,
        code="mi",
        vk_group_id=-100,
        name="Test ИНФО",
        telegram_channel=None,
        config={},
    )
    # Порядок session.execute() в регулярном пути _execute (до return dry_run):
    # 1 kind, 2 RegionConfig, 3 WorkTable(theme), 4 WorkTable(global),
    # 5 Region, 6 communities(theme), 7 comm_meta, 8 all_wt.
    results = [
        _res(first=("raion", {})),
        _res(sfirst=None),
        _res(sfirst=None),
        _res(sfirst=None),
        _res(sfirst=region_ns),
        _res(fall=[(123,)]),
        _res(fall=[(123, "Группа")]),
        _res(sall=[]),
    ]
    cm = _FakeSessionCM(results)

    post = {"owner_id": -123, "id": 5, "text": "hello", "attachments": []}
    bulletin_ns = SimpleNamespace(
        post_count=2,
        text="BULLETIN TEXT",
        attachments_list=[],
        posts_included=["lip1", "lip2"],
    )

    vk_client_inst = MagicMock()
    vk_client_inst.get_wall_posts.return_value = []
    parser_inst = MagicMock()
    parser_inst.parse_posts_from_communities = AsyncMock(return_value=[post])
    parser_inst.get_stats.return_value = {}
    splitter_inst = MagicMock()
    splitter_inst.split_posts.return_value = ([], [post])  # no mourning, one regular
    builder_inst = MagicMock()
    builder_inst.build_bulletin.return_value = bulletin_ns

    publisher_cls = MagicMock()
    publisher_cls.create_with_policy = AsyncMock()

    with (
        patch("database.connection.AsyncSessionLocal", lambda: cm),
        patch("tasks.parsing_scheduler_tasks.run_coro", lambda coro: asyncio.run(coro)),
        patch(
            "modules.vk_token_router.pick_healthy_read_token",
            AsyncMock(return_value=TokenCandidate(name="T", token="tok", source="user")),
        ),
        patch("modules.vk_monitor.vk_client.VKClient", return_value=vk_client_inst),
        patch("modules.vk_monitor.advanced_parser.AdvancedVKParser", return_value=parser_inst),
        patch("modules.publisher.bulletin_splitter.BulletinSplitter", return_value=splitter_inst),
        patch("modules.publisher.bulletin_builder.BulletinBuilder", return_value=builder_inst),
        patch(
            "modules.bulletin_pipeline_settings.get_effective_pipeline_settings",
            MagicMock(return_value={}),
        ),
        patch(
            "modules.publisher.postopus_bulletin_headers.resolve_bulletin_header",
            MagicMock(return_value="H"),
        ),
        patch(
            "modules.publisher.postopus_bulletin_headers.resolve_bulletin_hashtags",
            MagicMock(return_value=("#t", "#l")),
        ),
        patch("modules.publisher.vk_publisher_extended.VKPublisher", publisher_cls),
    ):
        out = st.parse_and_publish_theme.apply(
            args=("mi", "novost"), kwargs={"dry_run": True}
        ).get()

    assert out["dry_run"] is True
    assert out["success"] is True
    assert out["bulletins_count"] == 1
    assert len(out["would_publish"]) == 1
    preview = out["would_publish"][0]
    assert preview["kind"] == "regular"
    assert preview["post_count"] == 2
    assert preview["text_preview"].startswith("BULLETIN TEXT")
    # Главное: ничего не опубликовано и не закоммичено.
    publisher_cls.create_with_policy.assert_not_called()
    cm.session.commit.assert_not_called()


def test_cascaded_bulletin_accepts_dry_run_kwarg():
    """run_cascaded_bulletin принимает dry_run (сигнатурный гард для diagnostics)."""
    from modules.cascaded_bulletin import run_cascaded_bulletin

    sig = inspect.signature(run_cascaded_bulletin)
    assert "dry_run" in sig.parameters
    assert sig.parameters["dry_run"].default is False


def test_parse_and_publish_theme_accepts_dry_run_kwarg():
    """parse_and_publish_theme экспонирует dry_run (для diagnostics-эндпоинта)."""
    from tasks import parsing_scheduler_tasks as st

    # Под celery Task реальная функция — в .run; берём её сигнатуру.
    fn = getattr(st.parse_and_publish_theme, "run", st.parse_and_publish_theme)
    sig = inspect.signature(fn)
    assert "dry_run" in sig.parameters
    assert sig.parameters["dry_run"].default is False
