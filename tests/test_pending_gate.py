"""Гейт раскладки PENDING: сторож на то, ради чего он заведён (D-097).

Раскладка «индекс + файл на запись» ломается ровно одним молчаливым способом:
файл записи лежит на диске, а строки индекса нет. Память при этом выглядит целой
(файл-то есть), но запись больше не находится — её никто не читает, потому что
читают индекс. Обратное направление тише, но тоже вредно: строка ведёт в пустоту.

Гвозди этого файла:

* `test_orphan_body_is_caught` — главный класс отказа, ради которого гейт заведён;
* `test_cross_reference_is_not_a_duplicate` — проза законно ссылается на запись;
  гейт, считающий такую ссылку вторым вхождением в индекс, будет врать на ровном
  месте и его отключат;
* `test_unreadable_layout_is_not_success` — «не смог посмотреть» обязано отличаться
  от «сошлось» (G375): у отсутствующих файлов нет права на exit 0.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import pending_gate

HEADER = "# Pending follow-ups\n\n"


def build(tmp_path: Path, live_entries: str, hist_entries: str = "", bodies=("P001-alpha.md",)):
    """Разложить минимальный макет реестра и перенацелить на него гейт."""
    docs = tmp_path / "docs"
    pending = docs / "pending"
    pending.mkdir(parents=True)
    (docs / "PENDING_FOLLOWUPS.md").write_text(HEADER + live_entries, encoding="utf-8")
    (pending / "CLOSED.md").write_text("# История\n\n" + hist_entries, encoding="utf-8")
    for name in bodies:
        (pending / name).write_text(f"# {name}\n", encoding="utf-8")
    return docs


@pytest.fixture
def aim(monkeypatch, tmp_path):
    def _aim(live_entries, hist_entries="", bodies=("P001-alpha.md",)):
        docs = build(tmp_path, live_entries, hist_entries, bodies)
        monkeypatch.setattr(pending_gate, "ROOT", tmp_path)
        monkeypatch.setattr(pending_gate, "LIVE", docs / "PENDING_FOLLOWUPS.md")
        monkeypatch.setattr(pending_gate, "HIST", docs / "pending" / "CLOSED.md")
        monkeypatch.setattr(pending_gate, "BODIES", docs / "pending")

    return _aim


def run(*argv) -> int:
    import sys

    old = sys.argv
    sys.argv = ["pending_gate.py", *argv]
    try:
        return pending_gate.main()
    finally:
        sys.argv = old


class TestSound:
    def test_matching_layout_passes(self, aim):
        aim("- **P001** [Альфа](pending/P001-alpha.md) · `⏱ 2026-09-18`\n")
        assert run() == 0

    def test_closed_entry_counts_from_history_index(self, aim):
        """Закрытая запись живёт строкой в CLOSED.md — это не сирота."""
        aim(
            "- **P002** [Бета](pending/P002-beta.md)\n",
            hist_entries="- **P001** [Альфа](P001-alpha.md)\n",
            bodies=("P001-alpha.md", "P002-beta.md"),
        )
        assert run() == 0

    def test_cross_reference_is_not_a_duplicate(self, aim):
        """Проза законно ссылается на запись — это не вторая строка индекса."""
        aim("См. [пакет](pending/P001-alpha.md).\n\n" "- **P001** [Альфа](pending/P001-alpha.md)\n")
        assert run() == 0


class TestCatches:
    def test_orphan_body_is_caught(self, aim):
        """Запись на диске без строки индекса — она больше не находится."""
        aim(
            "- **P001** [Альфа](pending/P001-alpha.md)\n",
            bodies=("P001-alpha.md", "P002-beta.md"),
        )
        assert run() == 1

    def test_dangling_link_is_caught(self, aim):
        aim(
            "- **P001** [Альфа](pending/P001-alpha.md)\n"
            "- **P009** [Пропажа](pending/P009-gone.md)\n"
        )
        assert run() == 1

    def test_entry_number_must_match_file(self, aim):
        aim("- **P001** [Альфа](pending/P002-beta.md)\n", bodies=("P002-beta.md",))
        assert run() == 1

    def test_duplicate_index_line_is_caught(self, aim):
        aim(
            "- **P001** [Альфа](pending/P001-alpha.md)\n"
            "- **P001** [Альфа ещё раз](pending/P001-alpha.md)\n"
        )
        assert run() == 1

    def test_ceiling_is_enforced(self, aim):
        aim("- **P001** [Альфа](pending/P001-alpha.md)\n")
        assert run("--max-bytes", "10") == 1
        assert run() == 0


class TestCannotLook:
    def test_unreadable_layout_is_not_success(self, monkeypatch, tmp_path):
        """Файлов нет — это код 2, а не «сошлось»."""
        monkeypatch.setattr(pending_gate, "ROOT", tmp_path)
        monkeypatch.setattr(pending_gate, "LIVE", tmp_path / "docs" / "PENDING_FOLLOWUPS.md")
        monkeypatch.setattr(pending_gate, "HIST", tmp_path / "docs" / "pending" / "CLOSED.md")
        monkeypatch.setattr(pending_gate, "BODIES", tmp_path / "docs" / "pending")
        assert run() == 2

    def test_empty_body_dir_is_not_success(self, aim, tmp_path):
        aim("- **P001** [Альфа](pending/P001-alpha.md)\n", bodies=())
        assert run() == 2
