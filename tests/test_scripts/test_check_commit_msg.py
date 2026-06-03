"""Unit-тесты для ``scripts/check_commit_msg.py`` — commit-msg гейт качества.

Скрипт — CLI-утилита вне устанавливаемого пакета (см. pyproject
``[tool.setuptools.packages.find].exclude``), грузим напрямую через importlib,
как в ``tests/test_scripts/test_discover_scan.py``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

_spec = importlib.util.spec_from_file_location(
    "check_commit_msg", REPO_ROOT / "scripts" / "check_commit_msg.py"
)
ccm = importlib.util.module_from_spec(_spec)
sys.modules["check_commit_msg"] = ccm
_spec.loader.exec_module(ccm)


# --------------------------------------------------------------------------- #
# Валидные сообщения
# --------------------------------------------------------------------------- #


def test_feat_with_body_ok():
    msg = "feat(scope): добавить фичу\n\nЧто и почему.\n"
    assert ccm.check(msg) == []


def test_fix_with_body_and_trailer_ok():
    msg = (
        "fix(ad-cabinet): чинит баг\n\n"
        "Тело с объяснением.\n\n"
        "Co-Authored-By: Someone <x@y.z>\n"
    )
    assert ccm.check(msg) == []


def test_docs_without_body_ok():
    # docs/chore/test — тело не обязательно.
    assert ccm.check("docs: поправить README\n") == []
    assert ccm.check("chore: бамп зависимостей\n") == []


def test_breaking_and_scopeless_ok():
    assert ccm.check("feat!: ломающее\n\nтело\n") == []
    assert ccm.check("refactor: без скоупа\n\nтело\n") == []


def test_comments_and_scissors_ignored():
    msg = (
        "feat(x): описание\n\n"
        "тело\n"
        "# это комментарий git\n"
        "# ------------------------ >8 ------------------------\n"
        "# diff payload игнорируется\n"
    )
    assert ccm.check(msg) == []


def test_merge_and_revert_skipped():
    assert ccm.check("Merge branch 'main' into feature\n") == []
    assert ccm.check('Revert "feat: что-то"\n') == []
    assert ccm.check("fixup! feat: предыдущий\n") == []


# --------------------------------------------------------------------------- #
# Невалидные сообщения
# --------------------------------------------------------------------------- #


def test_non_conventional_subject_rejected():
    errs = ccm.check("починил баг в логине\n")
    assert errs
    assert any("Conventional" in e for e in errs)


def test_unknown_type_rejected():
    errs = ccm.check("wip: набросок\n\nтело\n")
    assert any("Conventional" in e for e in errs)


def test_feat_without_body_rejected():
    errs = ccm.check("feat(scope): только subject\n")
    assert any("тело" in e for e in errs)


def test_feat_body_only_trailers_rejected():
    # Только trailer после subject — это не тело.
    msg = "fix: что-то\n\nCo-Authored-By: X <x@y.z>\n"
    errs = ccm.check(msg)
    assert any("тело" in e for e in errs)


def test_missing_blank_separator_rejected():
    errs = ccm.check("feat: subj\nсразу тело без пустой строки\n")
    assert any("пустая строка" in e for e in errs)


def test_empty_message_rejected():
    assert ccm.check("\n\n") == ["пустое сообщение коммита"]


# --------------------------------------------------------------------------- #
# CLI-обёртка
# --------------------------------------------------------------------------- #


def test_main_accepts_valid_file(tmp_path):
    f = tmp_path / "COMMIT_EDITMSG"
    f.write_text("feat(x): ok\n\nтело\n", encoding="utf-8")
    assert ccm.main(["check_commit_msg.py", str(f)]) == 0


def test_main_rejects_invalid_file(tmp_path):
    f = tmp_path / "COMMIT_EDITMSG"
    f.write_text("плохое сообщение\n", encoding="utf-8")
    assert ccm.main(["check_commit_msg.py", str(f)]) == 1


def test_main_usage_error_without_arg():
    assert ccm.main(["check_commit_msg.py"]) == 2
