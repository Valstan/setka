"""Рейтинг поста для отбора в корневую группу (звено 5, шаг 1).

Функция чистая и конфига не читает — alpha приходит аргументом. Поэтому
здесь же живёт гейт «при alpha=0.5 рейтинг вырождается в нынешнюю
post_popularity»: это единственная гарантия, что мы не поменяли молча
сортировку ленты, которая на post_popularity висит.
"""

import pytest

from utils.post_utils import post_popularity, post_rating


def test_alpha_half_reproduces_post_popularity():
    for views, likes, comments, reposts in [
        (100, 5, 2, 1),
        (1224, 7, 0, 0),
        (0, 3, 0, 0),
        (0, 0, 0, 0),
        (17, 7, 0, 0),
    ]:
        assert post_rating(views, likes, comments, reposts, alpha=0.5) == pytest.approx(
            post_popularity(views, likes, comments, reposts)
        )


def test_lower_alpha_lifts_wide_reach_over_small_group():
    # Числа из спеки: районный хит против маленькой группы.
    big = (10000, 100, 0, 0)
    small = (20, 12, 0, 0)
    assert post_rating(*big, alpha=0.5) < post_rating(*small, alpha=0.5)
    assert post_rating(*big, alpha=0.25) > post_rating(*small, alpha=0.25)


def test_alpha_zero_is_pure_engagement():
    assert post_rating(10000, 100, 0, 0, alpha=0.0) == pytest.approx(100.0)
    assert post_rating(20, 12, 0, 0, alpha=0.0) == pytest.approx(12.0)


def test_views_none_gives_no_score():
    # Посты без views (10% выборки) рейтинга не получают: делитель схлопнулся
    # бы в 1, и пост без единого просмотра обогнал бы районный хит.
    assert post_rating(None, 50, 0, 0, alpha=0.25) is None


def test_missing_reactions_count_as_zero_not_as_missing():
    # None у реакций — это «ВК не прислал поле», трактуем нулём: в отличие от
    # views оно не стоит в знаменателе и рейтинг не искажает.
    assert post_rating(100, None, None, None, alpha=0.25) == pytest.approx(0.0)


def test_weights_are_the_project_convention():
    # лайк 1 · коммент 2 · репост 3
    assert post_rating(0, 1, 0, 0, alpha=0.0) == pytest.approx(1.0)
    assert post_rating(0, 0, 1, 0, alpha=0.0) == pytest.approx(2.0)
    assert post_rating(0, 0, 0, 1, alpha=0.0) == pytest.approx(3.0)


def test_alpha_from_config_defaults_to_quarter(monkeypatch):
    from config.classifier import get_rating_views_alpha

    monkeypatch.delenv("RATING_VIEWS_ALPHA", raising=False)
    assert get_rating_views_alpha() == pytest.approx(0.25)

    monkeypatch.setenv("RATING_VIEWS_ALPHA", "0.5")
    assert get_rating_views_alpha() == pytest.approx(0.5)


def test_vk_post_datetime_converts_unix_to_naive_utc():
    from datetime import datetime

    from utils.post_utils import vk_post_datetime

    assert vk_post_datetime(1787136000) == datetime(2026, 8, 19, 10, 40)


def test_vk_post_datetime_returns_none_on_anything_broken():
    # Подставленная «сейчас» обманула бы отсев по старости в нашу пользу.
    from utils.post_utils import vk_post_datetime

    for bad in (None, 0, "", "не число", [], 10**20):
        assert vk_post_datetime(bad) is None, f"ts={bad!r}"


def test_broken_alpha_env_falls_back_to_default(monkeypatch):
    # Опечатка в env не должна ронять отбор — ошибочное значение читается
    # как «дефолт», а не как исключение посреди волны публикации.
    from config.classifier import get_rating_views_alpha

    monkeypatch.setenv("RATING_VIEWS_ALPHA", "не число")
    assert get_rating_views_alpha() == pytest.approx(0.25)
