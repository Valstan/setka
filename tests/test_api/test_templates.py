"""Tests for /api/templates CRUD (etap 4b)."""

from unittest.mock import MagicMock, patch

import pytest

from web.api import templates as templates_api

# ─── Pydantic validation ─────────────────────────────────────────


def test_template_in_requires_title_and_body():
    """Empty title or body should fail validation (no silent inserts)."""
    with pytest.raises(Exception):
        templates_api.TemplateIn(title="", body="x")
    with pytest.raises(Exception):
        templates_api.TemplateIn(title="x", body="")


def test_template_in_caps_title_length():
    """120-char hard cap protects the VARCHAR column."""
    long_title = "x" * 200
    with pytest.raises(Exception):
        templates_api.TemplateIn(title=long_title, body="ok")


def test_template_in_category_optional():
    """Category is optional — UI may leave it blank."""
    payload = templates_api.TemplateIn(title="t", body="b")
    assert payload.category is None
    assert payload.is_active is True


# ─── Endpoint behaviour with mocked AsyncSessionLocal ────────────


class _FakeSession:
    """Minimal AsyncSessionLocal stand-in for endpoint smoke tests."""

    def __init__(self, *, get_result=None, scalars_all=None):
        self._get_result = get_result
        self._scalars_all = scalars_all or []
        self.added = []
        self.deleted = []
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
        return result

    async def get(self, _model, _pk):
        return self._get_result

    def add(self, obj):
        self.added.append(obj)

    async def commit(self):
        self.committed = True

    async def refresh(self, obj):
        self.refreshed.append(obj)
        # Simulate DB-assigned id + timestamps
        from datetime import datetime

        if getattr(obj, "id", None) is None:
            obj.id = 42
        if getattr(obj, "created_at", None) is None:
            obj.created_at = datetime(2026, 5, 22, 12, 0, 0)
        if getattr(obj, "updated_at", None) is None:
            obj.updated_at = datetime(2026, 5, 22, 12, 0, 0)

    async def delete(self, obj):
        self.deleted.append(obj)


async def test_list_templates_filters_inactive_by_default():
    """Default list excludes inactive templates (UI dropdown should see only live ones)."""
    session = _FakeSession(scalars_all=[])
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        with patch("web.api.templates.select") as sel:
            stmt = MagicMock()
            stmt.order_by.return_value = stmt
            stmt.where.return_value = stmt
            sel.return_value = stmt

            await templates_api.list_templates(include_inactive=False)

    stmt.where.assert_called_once()


async def test_list_templates_returns_all_when_include_inactive():
    """`?include_inactive=1` skips the is_active filter (management page)."""
    session = _FakeSession(scalars_all=[])
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        with patch("web.api.templates.select") as sel:
            stmt = MagicMock()
            stmt.order_by.return_value = stmt
            stmt.where.return_value = stmt
            sel.return_value = stmt

            await templates_api.list_templates(include_inactive=True)

    stmt.where.assert_not_called()


async def test_create_template_persists_trimmed_payload():
    """POST trims title/body before inserting."""
    session = _FakeSession()
    payload = templates_api.TemplateIn(
        title="  Спасибо  ",
        body="  Текст ответа  ",
        category="thanks",
    )
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        result = await templates_api.create_template(payload)

    assert len(session.added) == 1
    tpl = session.added[0]
    assert tpl.title == "Спасибо"
    assert tpl.body == "Текст ответа"
    assert tpl.category == "thanks"
    assert session.committed is True
    assert result["id"] == 42


async def test_create_template_defaults_region_to_none():
    """Без region_id шаблон создаётся общим (region_id=None)."""
    session = _FakeSession()
    payload = templates_api.TemplateIn(title="t", body="b")
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        result = await templates_api.create_template(payload)
    assert session.added[0].region_id is None
    assert result["region_id"] is None


async def test_create_template_persists_region_id():
    """region_id из payload сохраняется (региональный шаблон)."""
    session = _FakeSession()
    payload = templates_api.TemplateIn(title="t", body="b", region_id=7)
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        result = await templates_api.create_template(payload)
    assert session.added[0].region_id == 7
    assert result["region_id"] == 7


async def test_update_template_sets_region_id():
    from database.models import MessageTemplate

    tpl = MessageTemplate(id=1, title="t", body="b", is_active=True, region_id=None)
    session = _FakeSession(get_result=tpl)
    payload = templates_api.TemplateIn(title="t2", body="b2", region_id=3)
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        result = await templates_api.update_template(1, payload)
    assert tpl.region_id == 3
    assert result["region_id"] == 3


async def test_list_templates_region_filter_applies_where():
    """`region_id` параметр добавляет where-условие (общие + региональные)."""
    session = _FakeSession(scalars_all=[])
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        with patch("web.api.templates.select") as sel:
            stmt = MagicMock()
            stmt.order_by.return_value = stmt
            stmt.where.return_value = stmt
            sel.return_value = stmt
            # include_inactive=True → пропускаем is_active-фильтр, остаётся только region.
            await templates_api.list_templates(include_inactive=True, region_id=5)
    stmt.where.assert_called_once()


async def test_update_template_404_when_missing():
    """PUT on unknown id raises 404."""
    from fastapi import HTTPException

    session = _FakeSession(get_result=None)
    payload = templates_api.TemplateIn(title="t", body="b")
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await templates_api.update_template(999, payload)
    assert exc.value.status_code == 404


async def test_delete_template_404_when_missing():
    from fastapi import HTTPException

    session = _FakeSession(get_result=None)
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        with pytest.raises(HTTPException) as exc:
            await templates_api.delete_template(999)
    assert exc.value.status_code == 404


async def test_delete_template_happy_path():
    from database.models import MessageTemplate

    tpl = MessageTemplate(id=1, title="t", body="b", is_active=True)
    session = _FakeSession(get_result=tpl)
    with patch.object(templates_api, "AsyncSessionLocal", return_value=session):
        result = await templates_api.delete_template(1)
    assert result == {"success": True}
    assert session.deleted == [tpl]
    assert session.committed is True
