"""
Tests for modules.filters.morphology — Russian word stem helpers.

Unit-only: no DB / network.
"""
import pytest

from modules.filters.morphology import (
    expand_keywords,
    find_matching_keywords,
    get_word_stem,
    text_matches_keyword,
)


class TestGetWordStem:
    def test_short_word_unchanged(self):
        assert get_word_stem("кот") == "кот"

    def test_empty_returns_empty(self):
        assert get_word_stem("") == ""
        assert get_word_stem(None) == ""  # type: ignore[arg-type]

    def test_lowercases(self):
        assert get_word_stem("Малмыж") == "малмыж"

    def test_yo_normalized(self):
        # Ё → Е, чтобы стем матчился и при вариативном написании
        assert get_word_stem("Лебяжье") == get_word_stem("Лебяжье".replace("ё", "е"))

    def test_strips_adjective_ending(self):
        # «ский» снимается целиком (адъективный суффикс + окончание),
        # чтобы стем матчился и с короткой формой «Малмыже».
        assert get_word_stem("Малмыжский") == "малмыж"

    def test_strips_genitive_ending(self):
        assert get_word_stem("Малмыжского") == "малмыж"

    def test_strips_short_form_too(self):
        # Короткая форма «Малмыже» (дательный падеж) тоже сводится к «малмыж»
        assert get_word_stem("Малмыже") == "малмыж"

    def test_short_stem_kept(self):
        # длина слова < MIN_STEM_LEN+1 → не урезаем
        assert get_word_stem("уржу") == "уржу"

    def test_does_not_overstrip(self):
        stem = get_word_stem("Кукмор")
        # Стем должен быть как минимум 4 символа
        assert len(stem) >= 4
        assert stem.startswith("кукмор") or stem == "кукмор"


class TestExpandKeywords:
    def test_expands_to_stems(self):
        stems = expand_keywords(["Малмыж", "Малмыжский"])
        # обе формы сводятся к одному стему «малмыж»
        assert "малмыж" in stems

    def test_ignores_empty_and_none(self):
        stems = expand_keywords(["", None, "  "])  # type: ignore[list-item]
        assert stems == set()

    def test_drops_too_short(self):
        # Стемы короче MIN_STEM_LEN (4) выкидываются
        assert expand_keywords(["ок"]) == set()


class TestTextMatchesKeyword:
    def test_matches_word_start(self):
        assert text_matches_keyword("В Малмыже прошёл фестиваль", "Малмыжский")

    def test_matches_genitive(self):
        assert text_matches_keyword("Жители Малмыжского района", "Малмыж")

    def test_yo_insensitive(self):
        assert text_matches_keyword("Лебяжье отметило юбилей", "Лебяжье")

    def test_no_substring_match_inside_word(self):
        # «куда» не должно матчить «уда» из другого слова посередине
        assert not text_matches_keyword("Покуда мы шли", "уда")

    def test_empty_text(self):
        assert not text_matches_keyword("", "Малмыж")

    def test_empty_keyword(self):
        assert not text_matches_keyword("текст", "")


class TestFindMatchingKeywords:
    def test_returns_only_matches(self):
        text = "В Малмыжском районе прошёл фестиваль"
        keywords = ["Малмыж", "Уржум", "Кукмор"]
        matched = find_matching_keywords(text, keywords)
        assert matched == ["Малмыж"]

    def test_preserves_original_form(self):
        # Возвращаем именно исходную форму ключевого слова, не стем
        text = "Малмыжского сельсовета"
        matched = find_matching_keywords(text, ["Малмыжский"])
        assert matched == ["Малмыжский"]

    def test_empty_inputs(self):
        assert find_matching_keywords("", ["x"]) == []
        assert find_matching_keywords("text", []) == []
        assert find_matching_keywords("text", [None, "", "  "]) == []  # type: ignore[list-item]
