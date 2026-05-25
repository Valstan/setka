"""Tests for /api/discovery — region auto-registration (big idea 2026-05-22)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from database.models import Community, CommunityCandidate
from web.api import discovery as discovery_api

# ─── Pydantic validation ─────────────────────────────────────────


def test_trigger_in_validates_categories():
    """Unknown category should be rejected before we even hit Groq."""
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, categories=["bogus_cat"])


def test_trigger_in_accepts_subset_of_allowed_categories():
    payload = discovery_api.TriggerIn(region_id=1, categories=["novost", "sport"])
    assert payload.categories == ["novost", "sport"]


def test_trigger_in_per_query_count_bounds():
    """per_query_count must stay within 10..1000."""
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, per_query_count=5)
    with pytest.raises(Exception):
        discovery_api.TriggerIn(region_id=1, per_query_count=2000)
    # Within bounds is fine.
    discovery_api.TriggerIn(region_id=1, per_query_count=100)


def test_candidate_patch_accepts_valid_status():
    p = discovery_api.CandidatePatch(status="approved", category="novost")
    assert p.status == "approved"


def test_candidate_patch_rejects_invalid_status():
    with pytest.raises(Exception):
        discovery_api.CandidatePatch(status="bogus")


def test_candidate_patch_normalises_status_case():
    p = discovery_api.CandidatePatch(status="APPROVED", category="novost")
    assert p.status == "approved"


def test_candidate_patch_rejects_unknown_category():
    with pytest.raises(Exception):
        discovery_api.CandidatePatch(status="approved", category="bogus_cat")


def test_candidate_patch_empty_category_becomes_none():
    p = discovery_api.CandidatePatch(status="rejected", category="")
    assert p.category is None


# ─── /cities — VK resolver smoke ─────────────────────────────────


async def test_resolve_city_returns_trimmed_items():
    fake_client = MagicMock()
    fake_client.resolve_city.return_value = [
        {"id": 314, "title": "Малмыж", "area": "Малмыжский р-н", "region": "Кировская обл."},
        {"id": 0, "title": "Skip me — no id"},  # должно отфильтроваться
        {"id": 200},  # минимум полей — заполняем пустыми строками
    ]
    with patch.object(discovery_api, "VK_TOKENS", {"VALSTAN": "token"}):
        with patch.object(discovery_api, "VKClient", return_value=fake_client):
            res = await discovery_api.resolve_city(q="Малмыж")
    items = res["items"]
    assert len(items) == 2
    assert items[0] == {
        "id": 314,
        "title": "Малмыж",
        "area": "Малмыжский р-н",
        "region": "Кировская обл.",
    }
    assert items[1] == {"id": 200, "title": "", "area": "", "region": ""}


async def test_resolve_city_503_when_no_token():
    with patch.object(discovery_api, "VK_TOKENS", {}):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.resolve_city(q="X")
    assert exc.value.status_code == 503


# ─── /trigger — endpoint orchestration ────────────────────────────


async def test_trigger_returns_runner_result_on_success():
    payload = discovery_api.TriggerIn(region_id=7, categories=None)
    fake_result = {"success": True, "region": "mi", "found": 5}
    session = _FakeSession()
    with (
        patch.object(
            discovery_api,
            "run_discovery_for_region_async",
            AsyncMock(return_value=fake_result),
        ),
        patch.object(discovery_api, "AsyncSessionLocal", return_value=session),
    ):
        out = await discovery_api.trigger_discovery(payload)
    assert out == fake_result
    # После успешного trigger endpoint обновляет regions.last_discovery_at
    # (для UI на /regions «когда последний раз искали» и beat-ротации).
    assert session.committed is True


async def test_trigger_does_not_update_last_discovery_at_on_failure():
    """При failed-result endpoint валит HTTPException 400 ДО UPDATE Region —
    last_discovery_at не должен обновляться, иначе UI покажет ложный
    «свежий» поиск, который на самом деле не дал результатов."""
    payload = discovery_api.TriggerIn(region_id=7)
    session = _FakeSession()
    with (
        patch.object(
            discovery_api,
            "run_discovery_for_region_async",
            AsyncMock(return_value={"success": False, "error": "no token"}),
        ),
        patch.object(discovery_api, "AsyncSessionLocal", return_value=session),
    ):
        with pytest.raises(HTTPException):
            await discovery_api.trigger_discovery(payload)
    assert session.committed is False


async def test_trigger_async_returns_task_id():
    """trigger-async ставит задачу в Celery и возвращает task_id для polling."""
    payload = discovery_api.TriggerIn(region_id=7)
    fake_task = MagicMock()
    fake_task.id = "task-abc-123"
    fake_task.state = "PENDING"
    fake_celery = MagicMock()
    fake_celery.send_task.return_value = fake_task
    with patch.dict("sys.modules", {"tasks.celery_app": MagicMock(app=fake_celery)}):
        out = await discovery_api.trigger_discovery_async(payload)
    assert out["task_id"] == "task-abc-123"
    assert out["state"] == "PENDING"
    assert out["region_id"] == 7
    fake_celery.send_task.assert_called_once()
    args, _ = fake_celery.send_task.call_args
    assert args[0] == "tasks.discovery_tasks.run_discovery_for_region"


async def test_task_status_success_returns_result():
    """Когда таска SUCCESS — endpoint возвращает result runner'а."""
    fake_ar = MagicMock()
    fake_ar.state = "SUCCESS"
    fake_ar.ready.return_value = True
    fake_ar.result = {"success": True, "region": "mi", "found": 5}
    with patch.dict(
        "sys.modules",
        {
            "tasks.celery_app": MagicMock(app=MagicMock()),
            "celery.result": MagicMock(AsyncResult=lambda tid, app: fake_ar),
        },
    ):
        out = await discovery_api.get_discovery_task_status("task-xyz")
    assert out["state"] == "SUCCESS"
    assert out["ready"] is True
    assert out["result"] == {"success": True, "region": "mi", "found": 5}
    assert out["error"] is None


async def test_task_status_failure_returns_error():
    """Когда таска FAILURE — endpoint возвращает текст ошибки в error."""
    fake_ar = MagicMock()
    fake_ar.state = "FAILURE"
    fake_ar.ready.return_value = True
    fake_ar.result = RuntimeError("boom")
    with patch.dict(
        "sys.modules",
        {
            "tasks.celery_app": MagicMock(app=MagicMock()),
            "celery.result": MagicMock(AsyncResult=lambda tid, app: fake_ar),
        },
    ):
        out = await discovery_api.get_discovery_task_status("task-fail")
    assert out["state"] == "FAILURE"
    assert out["ready"] is True
    assert out["result"] is None
    assert "boom" in out["error"]


async def test_task_status_pending_returns_no_result_no_error():
    """Pending/Started — ready=False, result и error None."""
    fake_ar = MagicMock()
    fake_ar.state = "STARTED"
    fake_ar.ready.return_value = False
    with patch.dict(
        "sys.modules",
        {
            "tasks.celery_app": MagicMock(app=MagicMock()),
            "celery.result": MagicMock(AsyncResult=lambda tid, app: fake_ar),
        },
    ):
        out = await discovery_api.get_discovery_task_status("task-pending")
    assert out["state"] == "STARTED"
    assert out["ready"] is False
    assert out["result"] is None
    assert out["error"] is None


async def test_trigger_translates_failure_to_http_400():
    payload = discovery_api.TriggerIn(region_id=7)
    with patch.object(
        discovery_api,
        "run_discovery_for_region_async",
        AsyncMock(return_value={"success": False, "error": "region not found"}),
    ):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.trigger_discovery(payload)
    assert exc.value.status_code == 400
    assert "region not found" in exc.value.detail


# ─── candidate PATCH ────────────────────────────────────────────


class _FakeSession:
    """AsyncSessionLocal stand-in (close enough for endpoint smoke tests)."""

    def __init__(self, *, get_result=None, scalars_all=None, execute_scalar=None):
        self._get_result = get_result
        self._scalars_all = scalars_all or []
        self._execute_scalar = execute_scalar
        self.added = []
        self.committed = False
        self.refreshed = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        scalars = MagicMock()
        scalars.all.return_value = self._scalars_all
        result = MagicMock()
        result.scalars.return_value = scalars
        result.scalar_one_or_none.return_value = self._execute_scalar
        return result

    async def get(self, _model, _pk):
        return self._get_result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)
        if getattr(obj, "id", None) is None:
            obj.id = 999


async def test_patch_candidate_404_when_missing():
    session = _FakeSession(get_result=None)
    payload = discovery_api.CandidatePatch(status="rejected")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(123, payload)
    assert exc.value.status_code == 404


async def test_patch_candidate_reject_just_updates_status():
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="rejected")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_candidate(1, payload)
    assert cand.status == "rejected"
    assert session.committed is True
    assert "candidate" in out


async def test_patch_candidate_approve_requires_category():
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
        ai_category=None,
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(1, payload)
    assert exc.value.status_code == 400
    assert "category" in exc.value.detail.lower()


async def test_patch_candidate_approve_uses_ai_category_when_payload_empty():
    """If client doesn't override category, fall back to AI suggestion."""
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="Test",
        status="pending",
        ai_category="novost",
    )
    session = _FakeSession(get_result=cand, execute_scalar=None)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_candidate(1, payload)
    assert cand.status == "approved"
    # Должно быть создано новое Community.
    assert len(session.added) == 1
    new_community = session.added[0]
    assert isinstance(new_community, Community)
    assert new_community.category == "novost"
    assert "candidate" in out


async def test_patch_candidate_approve_rejects_other_as_category():
    """'other' — это escape hatch, не валидная финальная категория."""
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
        ai_category="other",
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(status="approved")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(1, payload)
    assert exc.value.status_code == 400


# ─── PATCH category-only (re-categorise для inline-dropdown) ──────


async def test_patch_candidate_category_only_updates_ai_category():
    """Body `{category: 'novost'}` без status — re-categorise pending кандидата."""
    cand = CommunityCandidate(
        id=1,
        region_id=2,
        vk_id=10,
        name="X",
        status="pending",
        ai_category="sport",
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch(category="novost")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_candidate(1, payload)
    assert cand.ai_category == "novost"
    assert cand.status == "pending"  # status не тронули
    assert session.committed is True
    assert session.added == []  # Community НЕ создаём
    assert out["candidate"]["ai_category"] == "novost"


async def test_delete_candidate_404_when_missing():
    session = _FakeSession(get_result=None)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.delete_candidate(123)
    assert exc.value.status_code == 404
    assert session.committed is False


class _DeleteSession(_FakeSession):
    """Stand-in для DELETE — фиксирует, что cand был передан в session.delete()."""

    def __init__(self, *, get_result):
        super().__init__(get_result=get_result)
        self.deleted = []

    async def delete(self, obj):
        self.deleted.append(obj)


async def test_delete_candidate_success_removes_from_session():
    cand = CommunityCandidate(id=42, region_id=2, vk_id=10, name="X", status="rejected")
    session = _DeleteSession(get_result=cand)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.delete_candidate(42)
    assert out == {"deleted": 42}
    assert session.deleted == [cand]
    assert session.committed is True


async def test_delete_candidate_works_for_any_status():
    """DELETE — это hard-delete без оглядки на status (pending / approved /
    rejected / deferred — всё одинаково удаляется)."""
    for status in ("pending", "approved", "rejected", "deferred"):
        cand = CommunityCandidate(id=1, region_id=2, vk_id=10, name="X", status=status)
        session = _DeleteSession(get_result=cand)
        with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
            out = await discovery_api.delete_candidate(1)
        assert out == {"deleted": 1}
        assert session.deleted == [cand]


async def test_patch_candidate_empty_body_returns_400():
    cand = CommunityCandidate(
        id=1, region_id=2, vk_id=10, name="X", status="pending", ai_category="sport"
    )
    session = _FakeSession(get_result=cand)
    payload = discovery_api.CandidatePatch()  # ни status, ни category
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_candidate(1, payload)
    assert exc.value.status_code == 400


# ─── /resolve-vk-url ────────────────────────────────────────────


async def test_resolve_vk_url_garbage_input_400():
    with pytest.raises(HTTPException) as exc:
        await discovery_api.resolve_vk_url(url="это не ссылка")
    assert exc.value.status_code == 400


async def test_resolve_vk_url_club_id_returns_group_info():
    fake_client = MagicMock()
    fake_client.get_groups_by_ids.return_value = [
        {
            "id": 12345,
            "name": "Группа Имя",
            "screen_name": "myclub",
            "members_count": 1234,
            "photo_200": "http://example.com/p.jpg",
        }
    ]
    with patch.object(discovery_api, "VK_TOKENS", {"VALSTAN": "t"}):
        with patch.object(discovery_api, "VKClient", return_value=fake_client):
            out = await discovery_api.resolve_vk_url(url="https://vk.com/club12345")
    assert out["group_id"] == 12345
    assert out["name"] == "Группа Имя"
    assert out["screen_name"] == "myclub"


async def test_resolve_vk_url_screen_name_resolves_via_vk():
    """Screen-name URL → resolveScreenName → groups.getById."""
    fake_client = MagicMock()
    fake_client.vk.utils.resolveScreenName.return_value = {
        "type": "group",
        "object_id": 777,
    }
    fake_client.get_groups_by_ids.return_value = [
        {"id": 777, "name": "Карачев ИНФО", "screen_name": "karachev_info"}
    ]
    with patch.object(discovery_api, "VK_TOKENS", {"VALSTAN": "t"}):
        with patch.object(discovery_api, "VKClient", return_value=fake_client):
            out = await discovery_api.resolve_vk_url(url="https://vk.com/karachev_info")
    assert out["group_id"] == 777
    assert out["name"] == "Карачев ИНФО"


async def test_resolve_vk_url_unknown_screen_name_404():
    fake_client = MagicMock()
    fake_client.vk.utils.resolveScreenName.return_value = {"type": "user", "object_id": 123}
    with patch.object(discovery_api, "VK_TOKENS", {"VALSTAN": "t"}):
        with patch.object(discovery_api, "VKClient", return_value=fake_client):
            with pytest.raises(HTTPException) as exc:
                await discovery_api.resolve_vk_url(url="https://vk.com/somebody")
    assert exc.value.status_code == 404


# ─── /commit/{region_id} ─────────────────────────────────────────


async def test_commit_region_404_when_missing():
    from database.models import Region

    session = _FakeSession(execute_scalar=None)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.commit_region(region_id=42)
    assert exc.value.status_code == 404
    # Не убрать unused import warning, явный noqa-ref:
    _ = Region


async def test_commit_region_400_when_no_vk_group_id():
    from database.models import Region

    region = Region(id=1, code="test", name="Test", vk_group_id=None, is_active=False)
    session = _FakeSession(execute_scalar=region)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.commit_region(region_id=1)
    assert exc.value.status_code == 400
    assert "vk_group_id" in exc.value.detail.lower() or "главную" in exc.value.detail.lower()


class _CommitSession(_FakeSession):
    """Стенд для commit_region.

    Последовательность execute: (1) region lookup, (2) candidates list,
    (3..N) Community lookups внутри `_approve_candidate` — должны вернуть
    None, чтобы создавалась новая Community.
    """

    def __init__(self, region, candidates):
        super().__init__()
        self._region = region
        self._candidates = candidates
        self._exec_n = 0

    async def execute(self, _stmt):
        self._exec_n += 1
        result = MagicMock()
        if self._exec_n == 1:
            result.scalar_one_or_none.return_value = self._region
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        elif self._exec_n == 2:
            scalars = MagicMock()
            scalars.all.return_value = self._candidates
            result.scalars.return_value = scalars
            result.scalar_one_or_none.return_value = None
        else:
            # _approve_candidate ищет существующую Community — её нет
            result.scalar_one_or_none.return_value = None
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        return result


async def test_commit_region_400_when_no_candidates_with_category():
    from database.models import Region

    region = Region(id=1, code="test", name="T", vk_group_id=-555, is_active=False)
    # все pending кандидаты имеют ai_category=other или None — approve пропустит
    cands = [
        CommunityCandidate(
            id=1, region_id=1, vk_id=10, name="A", status="pending", ai_category="other"
        ),
        CommunityCandidate(
            id=2, region_id=1, vk_id=20, name="B", status="pending", ai_category=None
        ),
    ]
    session = _CommitSession(region, cands)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.commit_region(region_id=1)
    assert exc.value.status_code == 400


async def test_commit_region_happy_path():
    from database.models import Region

    region = Region(id=1, code="test", name="T", vk_group_id=-555, is_active=False)
    cands = [
        CommunityCandidate(
            id=1, region_id=1, vk_id=10, name="A", status="pending", ai_category="novost"
        ),
        CommunityCandidate(
            id=2, region_id=1, vk_id=20, name="B", status="pending", ai_category="sport"
        ),
        CommunityCandidate(
            id=3, region_id=1, vk_id=30, name="C", status="pending", ai_category="other"
        ),
    ]
    session = _CommitSession(region, cands)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.commit_region(region_id=1)
    assert out["communities_created"] == 2
    assert out["pending_left"] == 1  # "other" остался
    assert out["region_code"] == "test"
    assert region.is_active is True
    # Approved кандидаты должны быть помечены, "other" — нет
    assert cands[0].status == "approved"
    assert cands[1].status == "approved"
    assert cands[2].status == "pending"
    # Должно быть создано 2 Community
    assert len(session.added) == 2
    assert all(isinstance(c, Community) for c in session.added)


# ─── /regions/{code}/config — save & get ─────────────────────────


def _make_region_stub(*, code="test", config=None, name="Test", center_city="X"):
    """Региональный stub для конфиг-тестов. SQLAlchemy-like, мутабельный."""
    r = MagicMock()
    r.code = code
    r.name = name
    r.center_city = center_city
    r.config = config
    return r


async def test_patch_config_localities_normalises_input():
    region = _make_region_stub(config={})
    session = _FakeSession(execute_scalar=region)
    payload = discovery_api._DiscoveryConfigPatch(value="Тужа\nШешурга\nтужа")
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_region_discovery_config(
            code="tuzha", field="localities", body=payload
        )
    assert out["ok"] is True
    assert out["items"] == ["Тужа", "Шешурга"]  # dedup case-insensitive
    assert out["count"] == 2
    assert region.config["localities"] == ["Тужа", "Шешурга"]
    assert session.committed is True


async def test_patch_config_accepts_list():
    region = _make_region_stub(config={"localities": ["old"]})
    session = _FakeSession(execute_scalar=region)
    payload = discovery_api._DiscoveryConfigPatch(value=["новости", "ДТП"])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.patch_region_discovery_config(
            code="x", field="discovery_keywords", body=payload
        )
    assert out["items"] == ["новости", "ДТП"]
    assert region.config["discovery_keywords"] == ["новости", "ДТП"]
    # Existing fields not in body must survive.
    assert region.config["localities"] == ["old"]


async def test_patch_config_404_when_region_missing():
    session = _FakeSession(execute_scalar=None)
    payload = discovery_api._DiscoveryConfigPatch(value=["X"])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.patch_region_discovery_config(
                code="nope", field="localities", body=payload
            )
    assert exc.value.status_code == 404


async def test_patch_config_400_on_unknown_field():
    payload = discovery_api._DiscoveryConfigPatch(value=["X"])
    with pytest.raises(HTTPException) as exc:
        await discovery_api.patch_region_discovery_config(code="x", field="evil", body=payload)
    assert exc.value.status_code == 400


async def test_get_config_returns_parsed_lists():
    region = _make_region_stub(
        config={"localities": ["Тужа", "Шешурга"], "discovery_keywords": "новости,ДТП"}
    )
    session = _FakeSession(execute_scalar=region)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.get_region_discovery_config(code="tuzha")
    assert out["code"] == "test"
    assert out["localities"] == ["Тужа", "Шешурга"]
    assert out["discovery_keywords"] == ["новости", "ДТП"]


async def test_get_config_empty_when_not_configured():
    region = _make_region_stub(config=None)
    session = _FakeSession(execute_scalar=region)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.get_region_discovery_config(code="tuzha")
    assert out["localities"] == []
    assert out["discovery_keywords"] == []


async def test_get_config_404_when_missing():
    session = _FakeSession(execute_scalar=None)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.get_region_discovery_config(code="nope")
    assert exc.value.status_code == 404


# ─── /ai-batch — human-in-the-loop chunked LLM categorisation ────


class _AiBatchSession:
    """Session, которая на разных вызовах execute возвращает разные данные.

    Первый execute — Region (scalar_one_or_none).
    Второй execute — candidates list (scalars().all()).
    """

    def __init__(self, *, region, candidates):
        self._region = region
        self._candidates = candidates
        self._call_count = 0
        self.committed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def execute(self, _stmt):
        self._call_count += 1
        result = MagicMock()
        if self._call_count == 1:
            result.scalar_one_or_none.return_value = self._region
            scalars = MagicMock()
            scalars.all.return_value = []
            result.scalars.return_value = scalars
        else:
            scalars = MagicMock()
            scalars.all.return_value = self._candidates
            result.scalars.return_value = scalars
            result.scalar_one_or_none.return_value = None
        return result

    async def commit(self):
        self.committed = True


def _make_candidate(id_, *, status="pending", ai_category=None, ai_is_relevant=None):
    c = CommunityCandidate(
        id=id_,
        region_id=1,
        vk_id=1000 + id_,
        name=f"Group {id_}",
        description=f"desc {id_}",
        status=status,
        ai_category=ai_category,
        ai_is_relevant=ai_is_relevant,
    )
    c.id = id_
    return c


async def test_ai_batch_returns_chunk_with_prompt():
    region = _make_region_stub(
        code="tuzha", name="Тужа", config={"localities": ["Тужа", "Шешурга"]}
    )
    region.id = 1
    cands = [_make_candidate(i + 1) for i in range(5)]
    session = _AiBatchSession(region=region, candidates=cands)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.get_ai_batch(code="tuzha", chunk=0, size=3)
    assert out["region_code"] == "tuzha"
    assert out["chunk_index"] == 0
    assert out["chunks_total"] == 2  # 5 candidates / size=3 → 2 chunks
    assert out["total_pending_uncategorized"] == 5
    assert len(out["items"]) == 3
    assert out["items"][0]["id"] == 1
    # Prompt должен содержать локалитеты и кандидатов.
    assert "Тужа" in out["prompt"]
    assert "Шешурга" in out["prompt"]
    assert "Group 1" in out["prompt"]


async def test_ai_batch_empty_when_no_pending_uncategorized():
    region = _make_region_stub(code="x", name="X", config={})
    region.id = 1
    session = _AiBatchSession(region=region, candidates=[])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.get_ai_batch(code="x", chunk=0, size=30)
    assert out["total_pending_uncategorized"] == 0
    assert out["chunks_total"] == 0
    assert out["items"] == []
    assert out["prompt"] == ""


async def test_ai_batch_out_of_range_chunk():
    region = _make_region_stub(code="x", name="X", config={})
    region.id = 1
    cands = [_make_candidate(i + 1) for i in range(3)]
    session = _AiBatchSession(region=region, candidates=cands)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.get_ai_batch(code="x", chunk=99, size=30)
    assert out["items"] == []
    assert out["chunks_total"] == 1


async def test_ai_batch_404_when_region_missing():
    session = _AiBatchSession(region=None, candidates=[])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.get_ai_batch(code="nope", chunk=0, size=30)
    assert exc.value.status_code == 404


async def test_ai_batch_apply_updates_pending_candidates():
    region = _make_region_stub(code="tuzha", name="Тужа", config={})
    region.id = 1
    cands = [
        _make_candidate(1, status="pending"),
        _make_candidate(2, status="pending"),
    ]
    session = _AiBatchSession(region=region, candidates=cands)
    body = discovery_api._AiBatchApply(
        items=[
            {
                "id": 1,
                "category": "novost",
                "is_relevant": True,
                "confidence": 88,
                "reasoning": "ok",
            },
            {
                "id": 2,
                "category": "other",
                "is_relevant": False,
                "confidence": 20,
                "reasoning": "no",
            },
        ]
    )
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.apply_ai_batch(code="tuzha", body=body)
    assert out["updated"] == 2
    assert out["summary"]["relevant"] == 1
    assert out["summary"]["irrelevant"] == 1
    assert cands[0].ai_category == "novost"
    assert cands[0].ai_is_relevant is True
    assert cands[0].ai_confidence == 88
    assert cands[1].ai_is_relevant is False
    assert session.committed is True


async def test_ai_batch_apply_skips_non_pending_candidates():
    """approved/rejected — модератор уже решил, перетирать не должны."""
    region = _make_region_stub(code="x", name="X", config={})
    region.id = 1
    cands = [
        _make_candidate(1, status="approved"),
        _make_candidate(2, status="pending"),
    ]
    session = _AiBatchSession(region=region, candidates=cands)
    body = discovery_api._AiBatchApply(
        items=[
            {"id": 1, "category": "novost", "is_relevant": True, "confidence": 90},
            {"id": 2, "category": "sport", "is_relevant": True, "confidence": 70},
        ]
    )
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.apply_ai_batch(code="x", body=body)
    assert out["updated"] == 1
    assert out["skipped"] == 1
    # Approved кандидат не тронут.
    assert cands[0].ai_category is None


async def test_ai_batch_apply_reports_missing_ids():
    region = _make_region_stub(code="x", name="X", config={})
    region.id = 1
    session = _AiBatchSession(region=region, candidates=[])
    body = discovery_api._AiBatchApply(
        items=[{"id": 999, "category": "novost", "is_relevant": True}]
    )
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.apply_ai_batch(code="x", body=body)
    assert out["updated"] == 0
    assert 999 in out["missing_ids"]


async def test_ai_batch_apply_drops_unknown_category_silently():
    """Если LLM выдала непонятную категорию — игнорируем поле, БД остаётся."""
    body_item = discovery_api._AiBatchItem(id=1, category="bogus_category")
    assert body_item.category is None  # _norm_category нормализовал


async def test_ai_batch_apply_empty_items_returns_zero():
    body = discovery_api._AiBatchApply(items=[])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=None):
        out = await discovery_api.apply_ai_batch(code="x", body=body)
    assert out["updated"] == 0
    assert out["skipped"] == 0


async def test_ai_batch_status_calculates_progress():
    region = _make_region_stub(code="x", name="X", config={})
    region.id = 1
    cands = [
        _make_candidate(1, status="pending", ai_category="novost"),
        _make_candidate(2, status="pending", ai_is_relevant=False),
        _make_candidate(3, status="pending"),
        _make_candidate(4, status="pending"),
    ]
    session = _AiBatchSession(region=region, candidates=cands)
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        out = await discovery_api.ai_batch_status(code="x")
    assert out["total"] == 4
    assert out["processed"] == 2  # id=1 (category), id=2 (relevant=false)
    assert out["remaining"] == 2


async def test_ai_batch_status_404_when_region_missing():
    session = _AiBatchSession(region=None, candidates=[])
    with patch.object(discovery_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await discovery_api.ai_batch_status(code="nope")
    assert exc.value.status_code == 404
