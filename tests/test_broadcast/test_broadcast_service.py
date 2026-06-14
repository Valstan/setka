"""Тесты сервисного слоя рассылки: цели по умолчанию + имена картинок."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest

from modules.broadcast import service


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    async def execute(self, stmt):
        res = MagicMock()
        res.all.return_value = self._rows
        return res


def test_default_targets_sorted_by_name():
    rows = [(-100, "Бэ"), (-200, "Аэ"), (-300, None)]
    out = asyncio.run(service.default_targets(_FakeSession(rows)))
    # Отсортировано по имени (регистронезависимо), пустое имя — наверх.
    assert [t["group_id"] for t in out] == [-300, -200, -100]
    assert out[1] == {"group_id": -200, "name": "Аэ"}


def test_default_targets_skips_null_group_id():
    rows = [(-100, "A"), (None, "X")]
    out = asyncio.run(service.default_targets(_FakeSession(rows)))
    assert [t["group_id"] for t in out] == [-100]


def test_safe_image_name_ok():
    assert service.safe_image_name("photo.png") == "photo.png"
    assert service.safe_image_name("../../etc/x.JPG") == "x.JPG"


def test_safe_image_name_rejects_bad_ext():
    with pytest.raises(ValueError):
        service.safe_image_name("doc.txt")


def test_safe_image_name_rejects_hidden_and_empty():
    with pytest.raises(ValueError):
        service.safe_image_name(".secret.png")
    with pytest.raises(ValueError):
        service.safe_image_name("")


def test_broadcast_image_paths_filters_missing(tmp_path, monkeypatch):
    d = tmp_path / "broadcast"
    d.mkdir()
    (d / "a.png").write_bytes(b"x")
    monkeypatch.setattr(service, "broadcast_image_dir", lambda: d)
    paths = service.broadcast_image_paths(["a.png", "missing.png", "bad.txt"])
    assert [p.name for p in paths] == ["a.png"]
