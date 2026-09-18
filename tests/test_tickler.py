"""Будильник дат: сторож на то, ради чего он заведён (#152).

Правило пула: дата, названная вне машинно-читаемого списка, не срабатывает —
обещание выглядит выполненным, а сработать не может. У нас это уже случилось:
«G307 — срок 19.09, не начат» три сессии ехал в `SESSION_HANDOFF` и двигался
только тем, что человек перечитывал handoff глазами.

Гвозди этого файла:

* `test_done_rows_are_silent` — закрытая строка не должна шуметь, иначе будильник
  через месяц звенит про всё сразу и его перестают читать (та же болезнь, что у
  меток старения в PENDING — pool #033);
* `test_check_fails_for_absent_date` — «проверка на замыкание» обязана падать,
  иначе она бесполезна: её смысл в том, чтобы после письма сгрепать дату обратно
  и получить отказ, если её не вписали;
* `test_broken_row_does_not_explode` — хук печатает это при каждом входе в сессию;
  битая строка в таблице не имеет права ронять старт.
"""

from __future__ import annotations

from datetime import date
from typing import List

from scripts.tickler import Item, main, overdue, parse, render, upcoming

TABLE = """
| Дата | Что обещано | Кому | Состояние |
|---|---|---|---|
| 2026-09-19 | G307 — замер доли неудач | brain (`ack: line`) | ✅ 2026-09-18 — ушло досрочно |
| 2026-09-20 | Зонная cookie — строка | brain (`ack: line`) | открыто |
| 2026-09-26 | D-095 — приёмка клиента | brain | открыто — ждём redirect_uri |
| 2026-10-30 | Состязательный аудит ЕСА | brain | открыто |
| не-дата | мусорная строка | никому | открыто |
"""

TODAY = date(2026, 9, 22)


class TestParse:
    def test_reads_only_dated_rows(self):
        items = parse(TABLE)
        assert [str(i.due) for i in items] == [
            "2026-09-19",
            "2026-09-20",
            "2026-09-26",
            "2026-10-30",
        ]

    def test_broken_row_does_not_explode(self):
        """Хук зовётся при каждом входе в сессию — падать ему нельзя."""
        assert parse("| не-дата | что | кому | открыто |") == []
        assert parse("") == []
        assert parse("| 2026-13-45 | битая дата | кому | открыто |") == []

    def test_state_decides_done_not_the_date(self):
        items = {i.what: i for i in parse(TABLE)}
        assert items["G307 — замер доли неудач"].done is True
        assert items["Зонная cookie — строка"].done is False

    def test_strikethrough_counts_as_history(self):
        assert Item(date(2026, 1, 1), "x", "y", "~~снято~~").done is True


class TestWindows:
    def test_overdue_is_past_and_open(self):
        assert [str(i.due) for i in overdue(parse(TABLE), TODAY)] == ["2026-09-20"]

    def test_done_rows_are_silent(self):
        """Закрытое не звенит — иначе будильник тонет в своей же истории."""
        late = overdue(parse(TABLE), date(2026, 9, 30))
        assert "G307 — замер доли неудач" not in [i.what for i in late]

    def test_upcoming_respects_horizon(self):
        soon = upcoming(parse(TABLE), TODAY, within=7)
        assert [str(i.due) for i in soon] == ["2026-09-26"]
        # За горизонтом — молчим: далёкая дата в отчёте каждой сессии есть шум.
        assert "2026-10-30" not in [str(i.due) for i in soon]

    def test_today_is_upcoming_not_overdue(self):
        items = parse("| 2026-09-22 | сегодняшнее | brain | открыто |")
        assert overdue(items, TODAY) == []
        assert [i.what for i in upcoming(items, TODAY, 7)] == ["сегодняшнее"]


class TestRender:
    def test_silence_when_nothing_due(self):
        """Пустая строка — договор с хуком: печатать нечего, значит молчим."""
        assert render(parse(TABLE), date(2026, 8, 1), within=7) == ""

    def test_overdue_is_marked_loudly_with_age(self):
        out = render(parse(TABLE), TODAY, within=7)
        assert "ПРОСРОЧЕНО на 2 дн." in out
        assert "Зонная cookie" in out

    def test_upcoming_says_when(self):
        out = render(parse(TABLE), TODAY, within=7)
        assert "через 4 дн." in out and "D-095" in out


class TestCheckClosesTheLoop:
    """Проверка на замыкание — по живому `docs/TICKLER.md`, но не по конкретной дате.

    ⚠️ Первая версия этих тестов держала литералы `2026-10-02` (открыта) и
    `2026-09-19` (закрыта) — и покраснела в тот же день: PR, закрывший 02.10,
    сделал тест ложным. Гейт сработал как надо, а тест был неправ: он проверял
    **состояние документа**, а не **поведение инструмента**. Дата в будильнике —
    движущаяся величина by design, привязываться к ней нельзя.
    """

    def _live(self) -> List[Item]:
        from scripts.tickler import TICKLER

        return parse(TICKLER.read_text(encoding="utf-8"))

    def test_check_passes_for_an_open_date_from_the_file(self):
        rows = [i for i in self._live() if not i.done]
        assert rows, "в будильнике нет ни одного открытого пункта — проверять нечего"
        assert main(["--check", str(rows[0].due)]) == 0

    def test_check_fails_for_absent_date(self):
        assert main(["--check", "2031-01-01"]) == 1

    def test_check_fails_for_a_closed_date_from_the_file(self):
        """Закрытая дата обещанием больше не является."""
        closed = [i for i in self._live() if i.done]
        assert closed, "в будильнике нет закрытых пунктов — проверять нечего"
        # Дата, у которой ВСЕ строки закрыты: иначе откроется соседняя с тем же днём.
        open_days = {i.due for i in self._live() if not i.done}
        fully_closed = [i for i in closed if i.due not in open_days]
        assert fully_closed, "у каждой закрытой даты есть открытый сосед — пропускаем"
        assert main(["--check", str(fully_closed[0].due)]) == 1

    def test_check_rejects_garbage(self):
        assert main(["--check", "позавчера"]) == 2

    def test_plain_run_never_fails(self):
        assert main(["--today", "2026-09-22"]) == 0
        assert main(["--today", "мусор"]) == 0
