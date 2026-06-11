"""Tests for scripts/deadcode_scan.py (#036): allowlist-сборка и парсинг known-файла."""

import scripts.deadcode_scan as dscan


def test_celery_allowlist_contains_decorated_and_beat_tasks():
    names = dscan.collect_celery_task_names()
    # Декорированная таска из tasks/celery_app.py
    assert "check_suggested_posts" in names
    # Таска, существующая только как строка в beat_schedule (чужой модуль)
    assert "run_all_regions_theme" in names
    # Защита от пустого результата при рефакторинге AST-обхода
    assert len(names) >= 10


def test_framework_fields_cover_pydantic_and_sqlalchemy():
    fields = dscan.collect_framework_field_names()
    # SQLAlchemy-колонка (database/models.py, Post)
    assert "ai_analysis_date" in fields
    # Методы фреймворк-классов собираться НЕ должны — остаются кандидатами
    assert "get_sources_by_category" not in fields


def test_load_known_parses_entries_and_skips_comments(tmp_path, monkeypatch):
    known_file = tmp_path / "known.txt"
    known_file.write_text(
        "# комментарий\n" "\n" "modules/foo.py::bar  # dead — хвост\n" "utils/baz.py::qux\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dscan, "KNOWN_FILE", known_file)
    known = dscan.load_known()
    assert known == {"modules/foo.py::bar": "dead — хвост", "utils/baz.py::qux": ""}


def test_load_known_missing_file_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(dscan, "KNOWN_FILE", tmp_path / "absent.txt")
    assert dscan.load_known() == {}
