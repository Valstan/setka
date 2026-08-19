"""Обновление метрик постов в окне 72 часов (звено 5, шаг 1).

Границы отбора проверяются на чистых функциях: «старше 72 часов не трогаем»
и «уже опубликованное нами не трогаем» — это правила владельца, и они должны
падать тестом, а не выясняться на счёте вызовов ВК.
"""

from modules.classifier.metrics_refresh import drop_already_published, ref_from_post_url


def test_ref_from_post_url_keeps_owner_sign():
    # lip теряет знак owner_id (abs), а wall.getById его требует. Знак
    # восстанавливаем из post_url, где он сохранён.
    assert ref_from_post_url("https://vk.com/wall-196153274_8272") == (-196153274, 8272)


def test_ref_from_broken_url_is_none():
    for bad in ("", None, "https://vk.com/id1", "https://vk.com/wallабв_1"):
        assert ref_from_post_url(bad) is None, f"url={bad!r}"


def test_drop_already_published_removes_ours_only():
    cands = [((-1, 10), "1_10"), ((-2, 20), "2_20"), ((-3, 30), "3_30")]
    out = drop_already_published(cands, {"2_20"})
    assert [lip for _, lip in out] == ["1_10", "3_30"]


def test_drop_already_published_with_empty_set_keeps_everything():
    cands = [((-1, 10), "1_10")]
    assert drop_already_published(cands, set()) == cands
