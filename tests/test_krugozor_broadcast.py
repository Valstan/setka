"""Тесты потока «Кругозор» (научпоп-ДАЙДЖЕСТ веером на стены регионов).

Покрывают конфиг и чистую логику движка: ротацию источников, выбор свежего,
сборку дайджеста (бюджет/max_items/грид фото), формат пункта, лид-фото, дедуп-cap.
БД/VK-функции (execute_krugozor_broadcast) — интеграционные, тут не трогаем.
"""

import database.models  # noqa: F401
import database.models_extended  # noqa: F401
from modules.krugozor_broadcast import (
    HEADER,
    LIP_HISTORY_MAX,
    _assemble_digest,
    _clean_text,
    _is_promo,
    _lead_photo,
    _make_block,
    _mark_seen,
    _newest_unseen,
    _rotation_order,
)

# --------------------------------------------------------------------------- #
# Конфиг
# --------------------------------------------------------------------------- #


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("KRUGOZOR_BROADCAST_DISABLED", raising=False)
    from config.runtime import krugozor_broadcast_disabled

    assert krugozor_broadcast_disabled() is True


def test_enabled_when_off(monkeypatch):
    monkeypatch.setenv("KRUGOZOR_BROADCAST_DISABLED", "0")
    from config.runtime import krugozor_broadcast_disabled

    assert krugozor_broadcast_disabled() is False


def test_default_source_category(monkeypatch):
    monkeypatch.delenv("KRUGOZOR_SOURCE_CATEGORY", raising=False)
    from config.runtime import get_krugozor_source_category

    assert get_krugozor_source_category() == "krugozor"


def test_target_region_codes_parse(monkeypatch):
    monkeypatch.setenv("KRUGOZOR_TARGET_REGION_CODES", " Malmyzh , arbazh ")
    from config.runtime import get_krugozor_target_region_codes

    assert get_krugozor_target_region_codes() == {"malmyzh", "arbazh"}


def test_source_exclude_ids_parse(monkeypatch):
    monkeypatch.setenv("KRUGOZOR_SOURCE_EXCLUDE_IDS", "-65614662, garbage, -85330")
    from config.runtime import get_krugozor_source_exclude_ids

    assert get_krugozor_source_exclude_ids() == {-65614662, -85330}


def test_digest_config_defaults(monkeypatch):
    for k in (
        "KRUGOZOR_DIGEST_MAX_ITEMS",
        "KRUGOZOR_SNIPPET_LEN",
        "KRUGOZOR_TEXT_BUDGET",
        "KRUGOZOR_DIGEST_PHOTOS",
        "KRUGOZOR_MAX_POST_AGE_HOURS",
    ):
        monkeypatch.delenv(k, raising=False)
    from config.runtime import (
        get_krugozor_digest_max_items,
        get_krugozor_max_post_age_hours,
        get_krugozor_snippet_len,
        get_krugozor_text_budget,
        krugozor_digest_photos_enabled,
    )

    assert get_krugozor_digest_max_items() == 4
    assert get_krugozor_snippet_len() == 500
    assert get_krugozor_text_budget() == 3500
    assert krugozor_digest_photos_enabled() is True
    assert get_krugozor_max_post_age_hours() == 72.0


# --------------------------------------------------------------------------- #
# Ротация источников (round-robin для разносола)
# --------------------------------------------------------------------------- #


def test_rotation_starts_after_cursor():
    assert _rotation_order(4, 1) == [2, 3, 0, 1]


def test_rotation_fresh_cursor_starts_at_zero():
    assert _rotation_order(4, -1) == [0, 1, 2, 3]


def test_rotation_edge_cases():
    assert _rotation_order(0, 5) == []
    assert _rotation_order(1, 0) == [0]


# --------------------------------------------------------------------------- #
# Выбор свежего непосланного поста
# --------------------------------------------------------------------------- #


def _post(owner_id, pid, age_seconds, now=1_000_000):
    return {"owner_id": owner_id, "id": pid, "date": now - age_seconds}


def test_newest_unseen_picks_freshest():
    now = 1_000_000
    posts = [_post(-100, 1, 3600, now), _post(-100, 2, 60, now), _post(-100, 3, 7200, now)]
    assert _newest_unseen(posts, set(), 0, now)["id"] == 2


def test_newest_unseen_skips_seen_and_old():
    now = 1_000_000
    posts = [_post(-100, 2, 60, now), _post(-100, 1, 120, now)]
    assert _newest_unseen(posts, {"100_2"}, 0, now)["id"] == 1
    assert _newest_unseen([_post(-100, 9, 10_000, now)], set(), 3600, now) is None


def test_newest_unseen_reject_skips_to_next():
    now = 1_000_000
    posts = [_post(-100, 2, 60, now), _post(-100, 1, 120, now)]
    # reject самый свежий (id=2) → берём следующий (id=1)
    got = _newest_unseen(posts, set(), 0, now, reject=lambda p: p["id"] == 2)
    assert got["id"] == 1


# --------------------------------------------------------------------------- #
# Анти-промо фильтр
# --------------------------------------------------------------------------- #


def test_is_promo_marked_as_ads():
    assert _is_promo({"marked_as_ads": 1, "text": "наука"}) is True
    assert _is_promo({"marked_as_ads": True}) is True


def test_is_promo_legal_markers():
    assert _is_promo({"text": "Интересный факт. erid: 2Vfnxy"}) is True
    assert _is_promo({"text": "Партнёрский пост #реклама"}) is True
    assert _is_promo({"text": "На правах рекламы: курс"}) is True


def test_is_promo_clean_science_text_false():
    # научный текст с «ценой/скидкой» НЕ должен ложно срабатывать (нет commercial-scoring)
    assert (
        _is_promo({"text": "Цена нефти влияет на климатические модели, скидка энтропии"}) is False
    )
    assert _is_promo({"text": "Учёные открыли новый вид жуков"}) is False


def test_is_promo_non_dict():
    assert _is_promo("not a dict") is False


def test_promo_filter_config_default(monkeypatch):
    monkeypatch.delenv("KRUGOZOR_PROMO_FILTER", raising=False)
    from config.runtime import krugozor_promo_filter_enabled

    assert krugozor_promo_filter_enabled() is True


# --------------------------------------------------------------------------- #
# Формат пункта дайджеста
# --------------------------------------------------------------------------- #


def test_make_block_short_text_full():
    b = _make_block("ПостНаука", "https://vk.com/wall-1_2", "Короткий факт", 500)
    assert b == "📚 ПостНаука\nКороткий факт\n🔗 https://vk.com/wall-1_2"


def test_make_block_empty_text():
    assert _make_block("N+1", "u", "", 500) == "📚 N+1\n🔗 u"


def test_make_block_long_text_truncated():
    long = "слово " * 200  # ~1200 знаков
    b = _make_block("N+1", "u", long, 60)
    assert b.startswith("📚 N+1\n")
    assert b.endswith("…\n🔗 u")
    # тело укорочено около лимита (+ заголовок/ссылка/многоточие)
    assert len(b) < 60 + 40


def test_clean_text_collapses_blank_lines():
    assert _clean_text("a\n\n\n\nb\n  ") == "a\n\nb"


# --------------------------------------------------------------------------- #
# Лид-фото
# --------------------------------------------------------------------------- #


def test_lead_photo_extracts_first_photo():
    post = {"attachments": [{"type": "photo", "photo": {"owner_id": -100, "id": 5}}]}
    assert _lead_photo(post) == "photo-100_5"


def test_lead_photo_none_when_no_photo():
    assert (
        _lead_photo({"attachments": [{"type": "video", "video": {"owner_id": -1, "id": 2}}]})
        is None
    )
    assert _lead_photo({}) is None


# --------------------------------------------------------------------------- #
# Сборка дайджеста
# --------------------------------------------------------------------------- #


def _items(n):
    return [
        {
            "name": f"S{i}",
            "url": f"u{i}",
            "text": f"t{i}",
            "photo": (f"photo{i}" if i % 3 else None),
        }
        for i in range(n)
    ]


def test_assemble_all_fit_under_budget():
    items = [
        {"name": "A", "url": "u1", "text": "t1", "photo": "photoA"},
        {"name": "B", "url": "u2", "text": "t2", "photo": "photoB"},
        {"name": "C", "url": "u3", "text": "t3", "photo": None},
    ]
    text, att, used = _assemble_digest(
        items, snippet_len=500, text_budget=3500, max_items=4, photos_enabled=True
    )
    assert used == [0, 1, 2]
    assert text.startswith(HEADER + "\n\n")
    assert "📚 A" in text and "📚 B" in text and "📚 C" in text
    assert att == ["photoA", "photoB"]  # у C фото нет — в грид не попадает


def test_assemble_respects_max_items():
    text, att, used = _assemble_digest(
        _items(5), snippet_len=500, text_budget=3500, max_items=2, photos_enabled=True
    )
    assert used == [0, 1]
    assert text.count("📚 ") == 2


def test_assemble_photos_disabled():
    _t, att, _u = _assemble_digest(
        _items(3), snippet_len=500, text_budget=3500, max_items=4, photos_enabled=False
    )
    assert att == []


def test_assemble_budget_caps_items():
    big = [
        {"name": "A", "url": "u", "text": "x" * 1000, "photo": None},
        {"name": "B", "url": "u", "text": "y" * 1000, "photo": None},
    ]
    # бюджет вмещает только первый пункт (snippet_len велик, оба блока ~1000)
    _t, _a, used = _assemble_digest(
        big, snippet_len=1000, text_budget=1100, max_items=4, photos_enabled=True
    )
    assert used == [0]


def test_assemble_first_item_always_included_even_if_over_budget():
    huge = [{"name": "A", "url": "u", "text": "z" * 5000, "photo": None}]
    _t, _a, used = _assemble_digest(
        huge, snippet_len=5000, text_budget=100, max_items=4, photos_enabled=True
    )
    assert used == [0]  # первый — всегда


# --------------------------------------------------------------------------- #
# Дедуп-cap и регистрация beat
# --------------------------------------------------------------------------- #


class _FakeWT:
    def __init__(self, lip=None):
        self.lip = lip or []


def test_mark_seen_dedup_and_cap():
    wt = _FakeWT(lip=[str(i) for i in range(LIP_HISTORY_MAX)])
    _mark_seen(wt, "new_lip")
    assert len(wt.lip) == LIP_HISTORY_MAX
    assert wt.lip[-1] == "new_lip"
    assert "0" not in wt.lip


def test_mark_seen_no_duplicate():
    wt = _FakeWT(lip=["a", "b"])
    _mark_seen(wt, "b")
    assert wt.lip == ["a", "b"]


def test_krugozor_beat_registered():
    from tasks.celery_app import app

    assert "krugozor-broadcast-evening" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["krugozor-broadcast-evening"]
    assert entry["task"] == "tasks.parsing_scheduler_tasks.parse_and_publish_theme"
    assert entry["kwargs"] == {"region_code": "copy", "theme": "krugozor"}
