#!/usr/bin/env python3
"""Гейт раскладки PENDING: индекс и записи не должны расходиться (D-097, ADR-0013 мозга).

**Зачем.** `docs/PENDING_FOLLOWUPS.md` дорос до 987 КБ и читался целиком на каждом
`/start`. По мандату D-097 реестр разложен на «индекс + файл на запись»: живой
индекс — сам `PENDING_FOLLOWUPS.md`, история — `docs/pending/CLOSED.md`, тела —
`docs/pending/P<NNN>-<slug>.md`.

У такой раскладки ровно один способ сломаться молча: запись есть, а строки индекса
нет (или наоборот). Тогда запись перестаёт находиться — а файл на диске создаёт
ощущение, что память цела. Гейт сверяет, что их поровну, и держит потолок живого
индекса: как только он снова перерастёт `--max-bytes`, это значит, что закрытые
записи не уезжают в историю.

    python scripts/pending_gate.py            # тихо, exit 0 — расхождений нет
    python scripts/pending_gate.py --verbose  # печатает счёт даже когда всё цело

Коды возврата: 0 — сошлось; 1 — расхождение (гейт сработал); 2 — не смог
посмотреть (файлов нет, каталог не читается). «Не смог посмотреть» ≠ «сошлось».
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Dict, List, Set

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "docs" / "PENDING_FOLLOWUPS.md"
HIST = ROOT / "docs" / "pending" / "CLOSED.md"
BODIES = ROOT / "docs" / "pending"

# Строка индекса: "- **P042** [заголовок](pending/P042-slug.md) · `⏱ …`"
ENTRY = re.compile(r"^- \*\*(P\d{3})\*\* \[[^\]]*\]\((?:pending/)?(P\d{3}-[^)\s]+\.md)\)")
# Любая ссылка на тело записи — в том числе перекрёстная из прозы.
LINK = re.compile(r"\]\((?:pending/)?(P\d{3}-[^)\s]+\.md)\)")

DEFAULT_MAX_BYTES = 50 * 1024


def index_entries(path: Path) -> List[str]:
    """Строки индекса — по одной на запись. Перекрёстные ссылки из прозы сюда не попадают.

    Номер строки, разошедшийся с именем файла, возвращается с префиксом "!" —
    вызывающий превращает его в проблему, а не молча выбрасывает.
    """
    out: List[str] = []
    for line in path.read_text(encoding="utf-8").split("\n"):
        m = ENTRY.match(line)
        if not m:
            continue
        num, fname = m.group(1), m.group(2)
        out.append(fname if fname.startswith(num + "-") else f"!{num}:{fname}")
    return out


def all_links(path: Path) -> Set[str]:
    return set(LINK.findall(path.read_text(encoding="utf-8")))


def main() -> int:
    ap = argparse.ArgumentParser(description="гейт раскладки PENDING (D-097)")
    ap.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help="потолок живого индекса в байтах (по умолчанию 50 КБ)",
    )
    ap.add_argument("--verbose", action="store_true", help="печатать счёт и при успехе")
    args = ap.parse_args()

    for p in (LIVE, HIST):
        if not p.is_file():
            print(f"pending-gate: не нашёл {p.relative_to(ROOT)}", file=sys.stderr)
            return 2
    if not BODIES.is_dir():
        print(f"pending-gate: не нашёл каталог {BODIES.relative_to(ROOT)}", file=sys.stderr)
        return 2

    on_disk: Set[str] = {p.name for p in BODIES.glob("P*.md")}
    if not on_disk:
        print("pending-gate: в docs/pending нет ни одной записи", file=sys.stderr)
        return 2

    raw_entries = index_entries(LIVE) + index_entries(HIST)
    referenced = all_links(LIVE) | all_links(HIST)
    problems: List[str] = []

    for bad in [e for e in raw_entries if e.startswith("!")]:
        num, fname = bad[1:].split(":", 1)
        problems.append(f"строка индекса {num} ведёт на файл другого номера: {fname}")
    entries = [e for e in raw_entries if not e.startswith("!")]

    # 1. запись без строки индекса — её больше нельзя найти
    for name in sorted(on_disk - set(entries)):
        problems.append(f"запись есть, строки индекса нет: docs/pending/{name}")

    # 2. ссылка без записи — ведёт в пустоту
    for name in sorted(referenced - on_disk):
        problems.append(f"ссылка ведёт в пустоту: {name}")

    # 3. номер не должен встречаться дважды — номера не переиспользуются
    seen: Dict[str, str] = {}
    for name in sorted(on_disk):
        num = name.split("-", 1)[0]
        if num in seen:
            problems.append(f"номер {num} занят дважды: {seen[num]} и {name}")
        seen[num] = name

    # 4. одна запись — одна строка индекса (перекрёстные ссылки из прозы не в счёт)
    for name in sorted(set(entries)):
        if entries.count(name) > 1:
            problems.append(f"{name} стоит строкой индекса {entries.count(name)} раз(а)")

    # 5. потолок живого индекса
    live_size = LIVE.stat().st_size
    if live_size > args.max_bytes:
        problems.append(
            f"живой индекс {live_size} Б > потолка {args.max_bytes} Б — "
            "закрытые записи пора унести в docs/pending/CLOSED.md"
        )

    if problems:
        print("pending-gate: раскладка разошлась", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1

    if args.verbose:
        print(
            f"pending-gate: записей {len(on_disk)}, строк индекса {len(entries)}, "
            f"живой индекс {live_size} Б из {args.max_bytes} Б"
        )
    return 0


if __name__ == "__main__":
    # Гейт обязан падать громко: молчаливый exit 0 здесь означал бы «сошлось» там,
    # где на деле не удалось посмотреть (G375 — сеть безопасности должна различать
    # «нечего сказать» и «не смог сказать»).
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        print(f"pending-gate: не смог отработать ({type(exc).__name__}: {exc})", file=sys.stderr)
        sys.exit(2)
