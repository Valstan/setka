"""Синтаксис всех JS-файлов витрины — через ``node --check``.

Инцидент 2026-09-05: два метода в ``web/static/js/api.js`` вставлены без
запятых (объектный литерал), файл стал невалидным, и вся страница /ad потеряла
``apiClient``. Браузер владельца держал старую копию по кэш-версии ``?v=``,
поэтому поломка всплыла через часы после деплоя. Пропускается, если node нет.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

JS_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "js"
NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node не установлен")
@pytest.mark.parametrize("path", sorted(JS_DIR.glob("*.js")), ids=lambda p: p.name)
def test_js_file_parses(path: Path):
    res = subprocess.run([NODE, "--check", str(path)], capture_output=True, text=True, timeout=60)
    assert res.returncode == 0, res.stderr[-800:]
