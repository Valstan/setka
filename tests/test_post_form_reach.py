"""Замер «одиночный против сводки»: сторож на то, ради чего он заведён.

Замер существует, чтобы **не повторить** ошибку 26.08: там форма и содержание
изменились вместе, и одно наблюдение выдали бы за доказательство. Поэтому гвозди
здесь не про арифметику, а про то, что делает замер честным:

* `test_only_days_with_both_forms_are_compared` — сравнение идёт внутри пары
  «регион + день»; день, где вышла одна форма, сравнивать не с чем, и он обязан
  выпасть, а не подмешаться к чужому дню;
* `test_outlier_does_not_move_the_median` — метрика обязана быть медианой: один
  вирусный пост на 717 тысяч просмотров — это ровно тот случай, из-за которого
  замер и понадобился;
* `test_zero_median_is_counted_not_swallowed` — день, где сводки собрали ноль
  просмотров, нельзя ни поделить, ни тихо выбросить: он считается отдельно.
"""

from __future__ import annotations

from scripts.probe_post_form_reach import item_count, paired_days, post_form, summarize

MARK = "✍"


def row(region, day, form, views):
    return {"region": region, "day": day, "form": form, "views": views}


class TestForm:
    def test_marker_counts_items(self):
        assert item_count(f"Новости: {MARK} раз") == 1
        assert item_count(f"Новости: {MARK} раз {MARK} два {MARK} три") == 3
        assert item_count("без маркера") == 0

    def test_form_from_text(self):
        assert post_form(f"{MARK} одна новость") == "single"
        assert post_form(f"{MARK} раз {MARK} два") == "digest"

    def test_foreign_post_is_not_classified(self):
        """Чужая вёрстка (репост, ручной пост) — не наша форма, в замер не идёт."""
        assert post_form("просто текст") is None
        assert post_form("") is None


class TestPairing:
    def test_only_days_with_both_forms_are_compared(self):
        rows = [
            row("mi", "2026-08-26", "single", 100),
            row("mi", "2026-08-26", "digest", 50),
            row("mi", "2026-08-27", "single", 200),  # в этот день сводок не было
            row("kirs", "2026-08-26", "digest", 10),  # а тут не было одиночных
        ]
        pairs = paired_days(rows)
        assert [(p["region"], p["day"]) for p in pairs] == [("mi", "2026-08-26")]

    def test_pair_keeps_both_medians(self):
        rows = [
            row("mi", "2026-08-26", "single", 100),
            row("mi", "2026-08-26", "single", 300),
            row("mi", "2026-08-26", "digest", 40),
        ]
        p = paired_days(rows)[0]
        assert p["med_single"] == 200
        assert p["med_digest"] == 40
        assert (p["n_single"], p["n_digest"]) == (2, 1)

    def test_days_are_not_mixed_across_regions(self):
        """Один и тот же день в разных регионах — разные пары, а не общая куча."""
        rows = [
            row("mi", "2026-08-26", "single", 100),
            row("mi", "2026-08-26", "digest", 50),
            row("kirs", "2026-08-26", "single", 9),
            row("kirs", "2026-08-26", "digest", 3),
        ]
        assert len(paired_days(rows)) == 2


class TestSummary:
    def test_outlier_does_not_move_the_median(self):
        """Вирусный пост — причина замера, а не его результат."""
        normal = [
            {
                "region": "a",
                "day": "d1",
                "n_single": 1,
                "n_digest": 1,
                "med_single": 10,
                "med_digest": 10,
            },
            {
                "region": "b",
                "day": "d1",
                "n_single": 1,
                "n_digest": 1,
                "med_single": 12,
                "med_digest": 10,
            },
            {
                "region": "c",
                "day": "d1",
                "n_single": 1,
                "n_digest": 1,
                "med_single": 717853,
                "med_digest": 332,
            },
        ]
        s = summarize(normal)
        # медиана отношений: 1.0, 1.2, 2162 -> 1.2, а не среднее в 721
        assert s["mediana_otnosheniya"] == 1.2
        assert s["odinochnyy_vyigral"] == 2

    def test_zero_median_is_counted_not_swallowed(self):
        pairs = [
            {
                "region": "a",
                "day": "d1",
                "n_single": 1,
                "n_digest": 1,
                "med_single": 5,
                "med_digest": 0,
            },
        ]
        s = summarize(pairs)
        assert s["dney_bez_otnosheniya"] == 1
        assert s["mediana_otnosheniya"] is None
        assert s["odinochnyy_vyigral"] == 1

    def test_empty_input_is_not_a_result(self):
        s = summarize([])
        assert s["dney_s_oboimi_formami"] == 0
        assert s["mediana_otnosheniya"] is None
