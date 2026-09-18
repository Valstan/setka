#!/usr/bin/env python3
"""Будильник обещанных дат — единственная дверь для дат ([#152] пула, D-096).

**Зачем.** Дата, названная в письме мозгу или в абзаце `PENDING_FOLLOWUPS.md`,
не срабатывает: обещание выглядит выполненным (дата произнесена, обе стороны
спокойны), а сработать не может — читать её автоматически неоткуда. У нас это
уже случилось: три сессии подряд `SESSION_HANDOFF` носил «G307 — срок 19.09, не
начат», и двигало его только то, что человек перечитывал handoff глазами.

**Как.** [`docs/TICKLER.md`](../docs/TICKLER.md) — машинно-читаемая таблица дат.
Этот скрипт её печатает: просроченное и близкое. Зовётся SessionStart-хуком, то
есть при входе в сессию, чем бы владелец ни начал разговор.

Форма строки таблицы (первые четыре столбца обязательны):

    | YYYY-MM-DD | что обещано | кому | состояние |

`состояние` начинается с ✅ либо `~~` → строка историческая, будильник молчит.

**Проверка на замыкание** (рецепт #152): назвал дату в письме — сгрепай её
обратно из будильника, иначе обещания не было.

    python scripts/tickler.py --check 2026-10-02   # exit 0 — дата в списке; 1 — нет

Запуск без аргументов печатает просроченное и то, что наступит в ближайшие
``--within`` дней (по умолчанию 7). ``--today`` подменяет «сегодня» — нужен
тестам, чтобы они не зависели от календаря машины.

Скрипт никогда не валит старт сессии: любая неожиданность — молчаливый exit 0.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import List, NamedTuple, Optional

TICKLER = Path(__file__).resolve().parents[1] / "docs" / "TICKLER.md"

# Строка таблицы: | дата | что | кому | состояние | (пятый столбец не обязателен)
ROW = re.compile(
    r"^\|\s*(?P<due>\d{4}-\d{2}-\d{2})\s*\|"
    r"\s*(?P<what>[^|]*?)\s*\|"
    r"\s*(?P<whom>[^|]*?)\s*\|"
    r"\s*(?P<state>[^|]*?)\s*\|"
)


class Item(NamedTuple):
    due: date
    what: str
    whom: str
    state: str

    @property
    def done(self) -> bool:
        """Историческая строка: закрыта ✅ или зачёркнута."""
        return self.state.startswith("✅") or self.state.startswith("~~")


def parse(text: str) -> List[Item]:
    """Разобрать таблицу. Битую строку молча пропускаем — не роняем старт."""
    items: List[Item] = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        try:
            due = date.fromisoformat(m.group("due"))
        except ValueError:
            continue
        items.append(
            Item(
                due=due,
                what=m.group("what").strip(),
                whom=m.group("whom").strip(),
                state=m.group("state").strip(),
            )
        )
    return items


def overdue(items: List[Item], today: date) -> List[Item]:
    return sorted((i for i in items if not i.done and i.due < today), key=lambda i: i.due)


def upcoming(items: List[Item], today: date, within: int) -> List[Item]:
    horizon = today + timedelta(days=within)
    return sorted(
        (i for i in items if not i.done and today <= i.due <= horizon), key=lambda i: i.due
    )


def render(items: List[Item], today: date, within: int) -> str:
    """Текст для хука. Пусто — значит печатать нечего, и хук молчит."""
    late = overdue(items, today)
    soon = upcoming(items, today, within)
    if not late and not soon:
        return ""
    out = ["--- ⏰ будильник дат (docs/TICKLER.md)"]
    for i in late:
        days = (today - i.due).days
        out.append(f"  🔴 ПРОСРОЧЕНО на {days} дн. — {i.due} · {i.what} · {i.whom}")
    for i in soon:
        days = (i.due - today).days
        when = "СЕГОДНЯ" if days == 0 else f"через {days} дн."
        out.append(f"  ⏰ {when} — {i.due} · {i.what} · {i.whom}")
    return "\n".join(out)


def _read(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _utf8_stdout() -> None:
    """Печатать ⏰ и 🔴 на консоли, а не падать на них.

    Хук зовётся из Git Bash на Windows, где консоль по умолчанию **cp1251**:
    первый же emoji даёт `UnicodeEncodeError`. Поймано на живом прогоне 18.09 —
    и поймано не сразу, потому что глушитель внизу файла превращал падение в
    молчаливый `exit 0`, то есть в «будильнику нечего сказать».
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass


def main(argv=None) -> int:
    _utf8_stdout()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", metavar="YYYY-MM-DD", help="есть ли дата в будильнике")
    parser.add_argument("--within", type=int, default=7, help="горизонт «скоро», дней")
    parser.add_argument("--today", metavar="YYYY-MM-DD", help="подменить сегодня (тесты)")
    args = parser.parse_args(argv)

    text = _read(TICKLER)
    if text is None:
        # Файла нет — это не повод ронять сессию; на --check честный отказ.
        if args.check:
            print(f"будильника нет ({TICKLER.name}) — дата {args.check} не подтверждена")
            return 1
        return 0

    items = parse(text)

    if args.check:
        try:
            wanted = date.fromisoformat(args.check)
        except ValueError:
            print(f"--check: {args.check} — не дата в форме YYYY-MM-DD")
            return 2
        hits = [i for i in items if i.due == wanted and not i.done]
        if hits:
            for i in hits:
                print(f"✅ {i.due} в будильнике: {i.what} · {i.whom}")
            return 0
        print(f"🔴 {wanted} в будильнике НЕТ — значит обещания не было (#152)")
        return 1

    try:
        today = date.fromisoformat(args.today) if args.today else date.today()
    except ValueError:
        return 0

    text_out = render(items, today, args.within)
    if text_out:
        print(text_out)
    return 0


if __name__ == "__main__":
    # Сеть безопасности для хука — но НЕ глушитель. Прежняя версия отдавала здесь
    # `exit 0` на любую беду, и живой `UnicodeEncodeError` читался как «дат нет»,
    # а `--check` — как «дата подтверждена». Тот самый класс «успех инструмента ≠
    # выполненная работа», против которого этот файл и написан.
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - старт сессии ронять нельзя
        print(f"tickler: не смог отработать ({type(exc).__name__}: {exc})", file=sys.stderr)
        # «Не смог посмотреть» ≠ «дата на месте»: у --check нет права на 0.
        sys.exit(2 if "--check" in (sys.argv[1:] or []) else 0)
