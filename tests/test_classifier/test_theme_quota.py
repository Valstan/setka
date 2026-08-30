"""Квоты тем: потолок доли темы в ленте (заказ владельца 2026-08-30).

Владелец жаловался, что лентой не управляет: детсадов много, администрации мало.
Здесь стерегутся четыре вещи, каждая из которых ломается молча:

* **доли не нормируются на свою сумму** — иначе единственная заданная доля
  «новости=50» дала бы frac=1.0 и потолок исчез бы, не сказав ни слова;
* **запрет (доля 0) не протекает** ни через «не влезло в потолок», ни через
  правило непустой волны;
* **волна не пустеет молча** — задавив доминирующую тему, квота обязана вернуть
  хоть что-то, иначе район замолкает;
* **fail-open** — квота усилитель, а не точка отказа.
"""

from __future__ import annotations

import pytest

from modules.classifier.quota import apply_theme_quota, theme_caps


def _post(pid, theme, views=None):
    return {"id": pid, "theme": theme, "views": views}


def _theme_of(post):
    return post.get("theme")


def _rating_of(post):
    views = post.get("views")
    return None if views is None else float(views)


def _apply(posts, shares, published=None, *, slots=4, min_posts=1):
    return apply_theme_quota(
        posts,
        theme_of=_theme_of,
        rating_of=_rating_of,
        shares=shares,
        published=published or {},
        slots=slots,
        min_posts=min_posts,
    )


# ───────── арифметика потолка ─────────


def test_share_none_is_not_a_cap():
    # «Не ограничивать» — дефолт всем темам после миграции 090. Тема без доли не
    # должна появляться в потолках вовсе, иначе ноль в published сделал бы её cap
    # нулевым и лента встала бы целиком в день релиза.
    assert theme_caps({"новости": None}, {}, slots=4) == {}


def test_share_zero_is_a_hard_ban():
    assert theme_caps({"православие": 0}, {"новости": 10}, slots=4) == {"православие": 0}


def test_cap_counts_published_plus_this_wave():
    # total = 16 опубликовано + 4 места волны = 20; 50% → 10; уже вышло 6 → ещё 4.
    caps = theme_caps({"новости": 50}, {"новости": 6, "объявления": 10}, slots=4)
    assert caps["новости"] == 4


def test_cap_rounds_up_so_the_feed_does_not_stall():
    # В первые часы окна знаменатель мал: 5% от 4 мест это 0.2. Округление вниз
    # дало бы ноль всем темам сразу, и волна не вышла бы вовсе.
    assert theme_caps({"детсад": 5}, {}, slots=4)["детсад"] == 1


def test_exhausted_cap_never_goes_negative():
    assert theme_caps({"новости": 10}, {"новости": 99}, slots=4)["новости"] == 0


def test_shares_are_not_normalised_to_their_own_sum():
    # Единственная заданная доля 50% при 100 опубликованных — это потолок 52, а не
    # «всё разрешено». Нормировка на сумму долей превратила бы 50/50 в 1.0.
    caps = theme_caps({"новости": 50}, {"новости": 100}, slots=4)
    assert caps["новости"] == 0


# ───────── отбор внутри темы ─────────


def test_over_cap_keeps_the_best_rated():
    posts = [_post(1, "новости", 5), _post(2, "новости", 100), _post(3, "новости", 50)]
    kept, dropped = _apply(posts, {"новости": 25}, {"новости": 0}, slots=4)
    assert [p["id"] for p in kept] == [2]
    assert dropped == {"новости": 2}


def test_unmeasured_post_loses_to_a_measured_one():
    # Пост без просмотров — «не мерили», а не «ноль»: наверх он подниматься не
    # должен, иначе свежесобранный обгонит районный хит.
    posts = [_post(1, "новости", None), _post(2, "новости", 3)]
    kept, _ = _apply(posts, {"новости": 25}, {"новости": 0}, slots=4)
    assert [p["id"] for p in kept] == [2]


def test_input_order_is_preserved():
    # Пересортировкой занимается сборщик сводки; квота только вычитает, иначе её
    # результат нельзя сверить с логом отбора.
    posts = [_post(1, "новости", 1), _post(2, "новости", 99), _post(3, "новости", 50)]
    kept, _ = _apply(posts, {"новости": 100}, {}, slots=4)
    assert [p["id"] for p in kept] == [1, 2, 3]


def test_theme_without_cap_passes_through():
    posts = [_post(1, "культура", 1), _post(2, "новости", 1)]
    kept, _ = _apply(posts, {"новости": 100}, {}, slots=4)
    assert {p["id"] for p in kept} == {1, 2}


def test_post_without_theme_is_not_touched():
    # Вердикта нет (режим algorithmic-fallback или пост без текста) — квота о таком
    # посте ничего не знает и трогать его не вправе.
    posts = [_post(1, None, 1), _post(2, "православие", 1)]
    kept, _ = _apply(posts, {"православие": 0}, {}, slots=4)
    assert [p["id"] for p in kept] == [1]


# ───────── запрет не протекает ─────────


def test_banned_theme_is_dropped_entirely():
    posts = [_post(1, "православие", 100), _post(2, "православие", 99)]
    kept, dropped = _apply(posts, {"православие": 0}, {}, slots=4)
    assert kept == []
    assert dropped == {"православие": 2}


def test_ban_does_not_leak_through_the_non_empty_rule():
    # Волна состоит только из запрещённой темы. Правило «верни хоть что-то» не
    # должно её воскресить — иначе запрет обходится через чёрный ход.
    posts = [_post(1, "православие", 100), _post(2, "православие", 50)]
    kept, _ = _apply(posts, {"православие": 0}, {}, slots=4, min_posts=1)
    assert kept == []


# ───────── правило непустой волны ─────────


def test_emptied_wave_returns_the_best_allowed_post():
    # Квота исчерпана у всех тем: молчащая районная лента хуже перебора по доле.
    posts = [_post(1, "новости", 5), _post(2, "новости", 90)]
    kept, dropped = _apply(posts, {"новости": 10}, {"новости": 100}, slots=4)
    assert [p["id"] for p in kept] == [2]
    assert dropped == {"новости": 1}


def test_min_posts_zero_disables_the_rescue():
    posts = [_post(1, "новости", 5)]
    kept, _ = _apply(posts, {"новости": 10}, {"новости": 100}, slots=4, min_posts=0)
    assert kept == []


def test_rescue_does_not_fire_when_something_survived():
    posts = [_post(1, "новости", 5), _post(2, "культура", 90)]
    kept, _ = _apply(posts, {"новости": 10}, {"новости": 100}, slots=4)
    assert [p["id"] for p in kept] == [2]


# ───────── границы ─────────


def test_no_shares_means_no_quota():
    posts = [_post(1, "новости", 1)]
    assert _apply(posts, {})[0] == posts


def test_empty_input_is_returned_as_is():
    assert _apply([], {"новости": 10})[0] == []


def test_unparsable_share_is_ignored_not_fatal():
    # Доля приезжает из БД как NUMERIC; мусор в колонке не должен ронять волну.
    assert theme_caps({"новости": "не число"}, {}, slots=4) == {}


def test_dropped_counter_matches_what_actually_left():
    # Счётчик убранного считается ПОСЛЕ спасения — иначе лог разойдётся с тем,
    # что реально ушло в сводку.
    posts = [_post(1, "новости", 5), _post(2, "новости", 90), _post(3, "новости", 1)]
    kept, dropped = _apply(posts, {"новости": 10}, {"новости": 100}, slots=4)
    assert len(kept) + sum(dropped.values()) == len(posts)


def test_rescue_prefers_exhausted_theme_over_banned_one():
    # Два разных нуля в потолке: «квота исчерпана» и «тема запрещена». Когда волна
    # опустела, спасать можно только первую — даже если у запрещённой рейтинг выше.
    posts = [_post(1, "православие", 1000), _post(2, "новости", 5)]
    kept, _ = _apply(posts, {"православие": 0, "новости": 10}, {"новости": 100}, slots=4)
    assert [p["id"] for p in kept] == [2]


# ───────── врезка в отбор (сквозной путь) ─────────


@pytest.mark.asyncio
async def test_banned_theme_does_not_survive_the_wave(db_session, monkeypatch):
    """Сквозная проверка: запрет владельца доезжает до волны, а не только до
    чистой функции. Здесь ловится обрыв проводки — например если бы отбор
    по-прежнему читал только action и тему игнорировал, как было до этого PR."""
    from database.models_extended import ClassifierTheme
    from modules.classifier import selection

    db_session.add(ClassifierTheme(name="новости", position=1, share_percent=None))
    db_session.add(ClassifierTheme(name="православие", position=2, share_percent=0))
    await db_session.commit()

    monkeypatch.setenv("CLASSIFIER_SELECTION_ENABLED", "1")
    # Гейт потолков ВЫКЛЮЧЕН: запрет обязан работать и без него, иначе выключение
    # квот молча вернуло бы в ленту убранную владельцем рубрику.
    monkeypatch.delenv("CLASSIFIER_THEME_QUOTA_ENABLED", raising=False)

    async def fake_map(session, region_code):
        return {"100_1": "новости", "100_2": "православие"}

    monkeypatch.setattr(selection, "fetch_publish_map", fake_map)
    monkeypatch.setattr(selection, "decide_mode", lambda **kw: (selection.MODE_VERDICTS, False))

    posts = [
        {"owner_id": -100, "id": 1, "text": "районная новость"},
        {"owner_id": -100, "id": 2, "text": "богослужение"},
    ]
    out, mode, removed = await selection.apply_wave_selection(
        db_session, posts, region_code="mi", theme="novost"
    )
    assert [p["id"] for p in out] == [1]
    assert removed == 1


@pytest.mark.asyncio
async def test_service_themes_get_no_cap(db_session, monkeypatch):
    # «Мусор» не публикуется вовсе, «соседи» идут своим каналом — доля для них
    # была бы ручкой, ни к чему не подключённой, и обнулила бы соседский канал.
    from database.models_extended import ClassifierTheme
    from modules.classifier import selection

    db_session.add(ClassifierTheme(name="соседи", position=1, share_percent=0, is_service=True))
    await db_session.commit()

    shares = await selection._fetch_theme_shares(db_session)
    assert shares == {}
