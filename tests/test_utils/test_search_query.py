"""Тесты серверной части tiered-поиска (utils/search_query, brain pool #035)."""

from utils.search_query import (
    compact_number,
    convert_layout,
    normalize_query,
    query_variants,
    tokenize,
)

# ----------------------------------------------------------- normalize_query


def test_normalize_lowers_and_replaces_yo():
    assert normalize_query("  Ёлки  ЗЕЛЁНЫЕ ") == "елки зеленые"


def test_normalize_collapses_inner_spaces():
    assert normalize_query("клуб   малмыж") == "клуб малмыж"


def test_normalize_empty_and_none_safe():
    assert normalize_query("") == ""
    assert normalize_query(None) == ""


# ----------------------------------------------------------------- tokenize


def test_tokenize_multi_token():
    assert tokenize("Клуб  Малмыж") == ["клуб", "малмыж"]


def test_tokenize_blank_gives_empty():
    assert tokenize("   ") == []


# ----------------------------------------------------------- compact_number


def test_compact_number_strips_separators():
    assert compact_number("240-1") == "2401"
    assert compact_number("8 912 345") == "8912345"
    assert compact_number("240.1/2") == "24012"


def test_compact_number_rejects_text_tokens():
    assert compact_number("малмыж") is None
    assert compact_number("дв-1") is None  # цифр меньше половины
    assert compact_number("") is None


def test_compact_number_mixed_mostly_digits():
    # «а1234» — цифры преобладают, компактится
    assert compact_number("а1-2-3-4") == "а1234"


# ----------------------------------------------------------- convert_layout


def test_convert_layout_en_typed_russian():
    # «двигатель», набранный в EN-раскладке
    assert convert_layout("ldbufntkm") == "двигатель"


def test_convert_layout_ru_typed_english():
    # «test», набранный в RU-раскладке
    assert convert_layout("еуые") == "test"


def test_convert_layout_keeps_digits_and_spaces():
    assert convert_layout("rke, 240") == "клуб 240"


# ----------------------------------------------------------- query_variants


def test_query_variants_adds_layout_alternative():
    variants = query_variants("ldbufntkm")
    assert variants[0] == ["ldbufntkm"]
    assert variants[1] == ["двигатель"]


def test_query_variants_no_duplicate_when_layout_noop():
    # чистые цифры конвертация не меняет — второй вариант не плодим
    assert query_variants("2401") == [["2401"]]


def test_query_variants_empty_query():
    assert query_variants("") == []
    assert query_variants("   ") == []
