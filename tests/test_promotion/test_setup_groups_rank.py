"""Ранг мест считает упоминания места, а не подстроку.

11.09 у Арска в восьмёрку мест описания попало «Учили»: ранг считал
``ILIKE '%имя%'`` и засчитывал «научили» и «нас учили». Пока ранг только
сортировал список, это было терпимо; с 06.09 он пишется в публичное описание
группы — текст, который читают люди и индексирует поиск ВК.

Шаблон — регулярка Postgres (ARE). Тесты гоняют её модулем ``re``: конструкции,
которые она использует (группа с ``^``, отрицательный класс символов,
экранирование ``\\x``), в обоих движках значат одно и то же.
"""

import importlib.util
import os
import re

import pytest


def _load_script():
    spec = importlib.util.spec_from_file_location(
        "setup_groups_script_rank",
        os.path.join(os.path.dirname(__file__), "..", "..", "scripts", "setup_groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mod():
    return _load_script()


@pytest.fixture(scope="module")
def pattern(mod):
    return lambda place: re.compile(mod.mention_pattern(place))


class TestMentionPattern:
    def test_inside_another_word_is_not_a_mention(self, pattern):
        """Гвоздь: «научили» — не упоминание села Учили."""
        assert not pattern("Учили").search("нас научили читать")

    def test_lowercase_word_is_not_the_place(self, pattern):
        """Тот же набор букв со строчной — слово языка, а не село."""
        assert not pattern("Учили").search("учили детей плавать")

    def test_capitalized_place_counts(self, pattern):
        assert pattern("Учили").search("в Учили открыли школу")

    def test_case_endings_still_count(self, pattern):
        """Справа граница не нужна: падежные окончания — то же место."""
        p = pattern("Шемордан")
        assert p.search("сабантуй в Шемордане")
        assert p.search("дорога за Шеморданом")

    def test_start_of_text_counts(self, pattern):
        assert pattern("Арск").search("Арск готовится к празднику")

    def test_after_punctuation_counts(self, pattern):
        assert pattern("Арск").search("«Арск» и «Сабы»")
        assert pattern("Арск").search("вокзал г.Арск")

    def test_other_word_ending_in_the_name_is_not_a_mention(self, pattern):
        assert not pattern("Алан").search("Шалан и Каалан")

    def test_special_characters_are_literal(self, pattern):
        """Точка и скобки в названии — сами себя, а не часть регулярки."""
        assert pattern("Кзыл (Яр)").search("в Кзыл (Яр) приехали")
        assert pattern("Ст.Кукмор").search("перрон Ст.Кукмор")
        assert not pattern("Ст.Кукмор").search("перрон СтXКукмор")

    def test_new_formula_only_drops_never_adds(self, mod):
        """Каждое совпадение новой формулы нашёл бы и прежний ILIKE.

        Правка сужает подсчёт и ничего не приносит сверх прежнего — значит,
        места, которые ранг поднимал честно, ниже подняться не могли за счёт
        новых ложных совпадений.
        """
        texts = [
            "нас научили читать",
            "в Учили открыли школу",
            "Шалан и Каалан",
            "сабантуй в Шемордане",
            "АРСК и Арск",
            "г.Арск",
        ]
        for place in ("Учили", "Алан", "Шемордан", "Арск"):
            compiled = re.compile(mod.mention_pattern(place))
            for text in texts:
                if compiled.search(text):
                    assert place.lower() in text.lower(), (place, text)
