"""Тесты правил сайта — файл на сайт (D-015, мандат brain §4).

Держим три вещи, из-за которых механизм вообще заведён: надстройка сайта идёт
после общей базы (иначе она не переопределяет), отсутствие файлов — не авария,
и разросшийся файл не утекает в промпт целиком.

Отдельно проверяется, что реальные файлы репозитория читаются и содержат то
решение, ради которого правила и появились: федеральный календарь — не история
Малмыжа. Тест на содержимое markdown выглядит непривычно, но правила — часть
поведения конвейера, а не документация: молча опустевший файл вернул бы прогон к
перекосу первого дня, и заметил бы это только читатель сайта.
"""

from __future__ import annotations

import pytest

from modules.conveyor import rules as rules_mod


@pytest.fixture()
def rules_dir(tmp_path):
    """Пустой каталог правил + отвязка от env, чтобы тест не зависел от машины."""
    return tmp_path


def test_no_files_is_empty_not_error(rules_dir):
    """Правил нет — пустая строка, а не исключение: сайт может работать без них."""
    assert rules_mod.load_rules("vmalmyzhe", rules_dir=rules_dir) == ""


def test_site_overlay_goes_after_base(rules_dir):
    """Надстройка сайта — ПОСЛЕ базы: модель весомее читает то, что идёт позже."""
    (rules_dir / rules_mod.BASE_FILE).write_text("общее правило", encoding="utf-8")
    (rules_dir / "vmalmyzhe.md").write_text("сайтовое правило", encoding="utf-8")

    out = rules_mod.load_rules("vmalmyzhe", rules_dir=rules_dir)

    assert out.index("общее правило") < out.index("сайтовое правило")
    assert "важнее общих" in out


def test_base_alone_is_enough(rules_dir):
    """Есть только база — она и уходит в промпт; пустого заголовка сайта нет."""
    (rules_dir / rules_mod.BASE_FILE).write_text("общее правило", encoding="utf-8")

    out = rules_mod.load_rules("vmalmyzhe", rules_dir=rules_dir)

    assert "общее правило" in out
    assert "Правила этого сайта" not in out


def test_other_site_does_not_leak(rules_dir):
    """Правила чужого сайта не подмешиваются: у ДК и портала разные вкусы."""
    (rules_dir / "vmalmyzhe.md").write_text("правило портала", encoding="utf-8")

    out = rules_mod.load_rules("kalinino", rules_dir=rules_dir)

    assert "правило портала" not in out


def test_empty_site_key_takes_base_only(rules_dir):
    """Пустой ключ сайта не читает файл ``.md`` из каталога — только базу."""
    (rules_dir / rules_mod.BASE_FILE).write_text("общее правило", encoding="utf-8")
    (rules_dir / ".md").write_text("мусор", encoding="utf-8")

    out = rules_mod.load_rules("", rules_dir=rules_dir)

    assert "общее правило" in out
    assert "мусор" not in out


def test_oversized_rules_are_capped(rules_dir):
    """Разросшийся файл обрезается: промпт уходит в платный вызов на каждый пост."""
    (rules_dir / rules_mod.BASE_FILE).write_text("я" * (rules_mod.MAX_RULES_CHARS + 500), "utf-8")

    out = rules_mod.load_rules("vmalmyzhe", rules_dir=rules_dir)

    assert len(out) <= rules_mod.MAX_RULES_CHARS


def test_env_override_wins(rules_dir, monkeypatch):
    """``CONVEYOR_RULES_DIR`` перекрывает каталог по умолчанию."""
    (rules_dir / rules_mod.BASE_FILE).write_text("правило из env-каталога", encoding="utf-8")
    monkeypatch.setenv("CONVEYOR_RULES_DIR", str(rules_dir))

    assert rules_mod.get_rules_dir() == rules_dir
    assert "правило из env-каталога" in rules_mod.load_rules("vmalmyzhe")


def test_repo_rules_are_present_and_wired(monkeypatch):
    """Файлы репозитория читаются дефолтным путём и несут решение первого прогона."""
    monkeypatch.delenv("CONVEYOR_RULES_DIR", raising=False)

    out = rules_mod.load_rules("vmalmyzhe")

    assert "Общие правила кластера" in out
    assert "Правила этого сайта" in out
    # То самое, ради чего механизм построен: федеральный календарь ≠ история района.
    # Проверяем однострочный фрагмент: markdown переносится по 90 колонок, и
    # проверка на фразу через перенос строки ломается от безобидной переверстки.
    assert "istoriya-malmyzha" in out
    assert "военно-исторический календарь" in out
