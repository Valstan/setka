#!/usr/bin/env python3
"""commit-msg хук: качество сообщения коммита для SETKA.

С упразднением ``DEV_HISTORY.md`` (ADR-0001) всю хронологию несёт commit
message + PR description. Этот хук — лёгкий гейт качества:

  1. **subject** обязан быть по Conventional Commits:
     ``<type>(scope): описание`` (scope и ``!`` опциональны), где
     ``type`` ∈ feat/fix/refactor/docs/chore/test/style/perf/build/ci/revert.
  2. для ``feat`` / ``fix`` / ``refactor`` обязательно **тело** (что и почему),
     отделённое от subject пустой строкой — не только subject и trailers.

Авто-сообщения git (``Merge …`` / ``Revert …`` / ``fixup!`` / ``squash!``)
пропускаются. Длину subject намеренно НЕ ограничиваем (в проекте описательные
англоязычные subject'ы бывают длиннее 72).

Подключается как ``commit-msg``-стейдж в ``.pre-commit-config.yaml``. Логика
вынесена в чистую ``check(text) -> list[str]`` ради юнит-тестов.
"""

from __future__ import annotations

import re
import sys
from typing import List

TYPES = (
    "feat",
    "fix",
    "refactor",
    "docs",
    "chore",
    "test",
    "style",
    "perf",
    "build",
    "ci",
    "revert",
)

# <type>(optional-scope)!: subject — пробел после двоеточия обязателен.
_SUBJECT_RE = re.compile(
    r"^(?:" + "|".join(TYPES) + r")(?:\([\w .\-/]+\))?!?: .+",
)

# Типы, для которых тело обязательно (значимая правка поведения/кода).
_BODY_REQUIRED = {"feat", "fix", "refactor"}

# Git-trailer вида ``Co-Authored-By: …`` / ``Signed-off-by: …`` — НЕ тело.
_TRAILER_RE = re.compile(r"^[A-Za-z][A-Za-z-]*: .+")

# Авто-сгенерированные сообщения, которые не нам судить.
_AUTO_RE = re.compile(r"^(Merge |Revert |fixup! |squash! |amend! )")


def _content_lines(text: str) -> List[str]:
    """Выкинуть git-комментарии (``#``) и всё после scissors-разделителя."""
    out: List[str] = []
    for line in text.splitlines():
        if line.startswith("#"):
            # Строка-ножницы ``# ------------------------ >8 ...`` и весь
            # diff-payload после неё git отрезает сам; нам комментарии не нужны.
            continue
        out.append(line)
    return out


def check(text: str) -> List[str]:
    """Вернуть список ошибок (пустой = сообщение валидно)."""
    errors: List[str] = []
    lines = _content_lines(text)

    # Убрать ведущие пустые строки.
    while lines and not lines[0].strip():
        lines.pop(0)

    if not lines:
        return ["пустое сообщение коммита"]

    subject = lines[0].rstrip()

    if _AUTO_RE.match(subject):
        return []  # авто-коммит — пропускаем

    if not _SUBJECT_RE.match(subject):
        errors.append(
            "subject не по Conventional Commits — ожидается "
            "'<type>(scope): описание', type ∈ {" + ", ".join(TYPES) + "}"
        )

    type_match = re.match(r"^([a-z]+)", subject)
    ctype = type_match.group(1) if type_match else ""

    if ctype in _BODY_REQUIRED:
        body = lines[1:]
        # После subject должна быть пустая строка-разделитель.
        if body and body[0].strip():
            errors.append("после subject нужна пустая строка перед телом")
        # Тело = хотя бы одна непустая строка, не являющаяся trailer'ом.
        has_body = any(ln.strip() and not _TRAILER_RE.match(ln.strip()) for ln in body)
        if not has_body:
            errors.append(
                "тип '" + ctype + "' требует тело коммита (что/почему), "
                "не только subject и trailers"
            )

    return errors


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: check_commit_msg.py <commit-msg-file>", file=sys.stderr)
        return 2
    try:
        with open(argv[1], encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        print("не прочитать " + argv[1] + ": " + str(exc), file=sys.stderr)
        return 2

    errors = check(text)
    if errors:
        print("✗ commit-msg отклонён:", file=sys.stderr)
        for err in errors:
            print("  - " + err, file=sys.stderr)
        print(
            "\nПример:\n  feat(scope): краткое описание\n\n" "  Что меняли и почему.\n",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
