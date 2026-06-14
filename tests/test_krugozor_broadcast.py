"""Тесты потока «Кругозор» (научпоп → веером на стены регионов).

Покрывают конфиг (гейт/категория/цели/исключения) и чистую логику движка:
ротацию источников, выбор свежего непосланного поста, футер, дедуп-cap.
БД/VK-функции (execute_krugozor_broadcast) — интеграционные, тут не трогаем.
"""

# Регистрируем модели (как в test_curation_recorder) — иначе SQLAlchemy-mapper
# падает на relationship при импорте модуля.
import database.models  # noqa: F401
import database.models_extended  # noqa: F401
from modules.krugozor_broadcast import (
    LIP_HISTORY_MAX,
    _build_footer,
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

    assert krugozor_broadcast_disabled() is True  # OFF по умолчанию (#008)


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


def test_target_region_codes_none_when_empty(monkeypatch):
    monkeypatch.delenv("KRUGOZOR_TARGET_REGION_CODES", raising=False)
    from config.runtime import get_krugozor_target_region_codes

    assert get_krugozor_target_region_codes() is None


def test_source_exclude_ids_parse(monkeypatch):
    monkeypatch.setenv("KRUGOZOR_SOURCE_EXCLUDE_IDS", "-65614662, garbage, -85330")
    from config.runtime import get_krugozor_source_exclude_ids

    assert get_krugozor_source_exclude_ids() == {-65614662, -85330}


def test_max_age_and_interval_defaults(monkeypatch):
    monkeypatch.delenv("KRUGOZOR_MAX_POST_AGE_HOURS", raising=False)
    monkeypatch.delenv("KRUGOZOR_POST_INTERVAL_SECONDS", raising=False)
    from config.runtime import get_krugozor_max_post_age_hours, get_krugozor_post_interval_seconds

    assert get_krugozor_max_post_age_hours() == 72.0
    assert get_krugozor_post_interval_seconds() == 5.0


# --------------------------------------------------------------------------- #
# Ротация источников (round-robin для разносола)
# --------------------------------------------------------------------------- #


def test_rotation_starts_after_cursor():
    assert _rotation_order(4, 1) == [2, 3, 0, 1]


def test_rotation_fresh_cursor_starts_at_zero():
    assert _rotation_order(4, -1) == [0, 1, 2, 3]


def test_rotation_wraps():
    assert _rotation_order(3, 2) == [0, 1, 2]


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
    posts = [
        _post(-100, 1, 3600, now),
        _post(-100, 2, 60, now),  # самый свежий
        _post(-100, 3, 7200, now),
    ]
    got = _newest_unseen(posts, seen=set(), max_age_seconds=0, now_ts=now)
    assert got["id"] == 2


def test_newest_unseen_skips_seen():
    now = 1_000_000
    posts = [_post(-100, 2, 60, now), _post(-100, 1, 120, now)]
    # lip самого свежего (100_2) уже разослан → берём следующий (100_1)
    got = _newest_unseen(posts, seen={"100_2"}, max_age_seconds=0, now_ts=now)
    assert got["id"] == 1


def test_newest_unseen_respects_max_age():
    now = 1_000_000
    posts = [_post(-100, 1, 10_000, now)]  # старее лимита
    assert _newest_unseen(posts, set(), max_age_seconds=3600, now_ts=now) is None


def test_newest_unseen_none_when_all_seen():
    now = 1_000_000
    posts = [_post(-100, 1, 60, now)]
    assert _newest_unseen(posts, {"100_1"}, 0, now) is None


def test_newest_unseen_ignores_malformed():
    now = 1_000_000
    posts = [{"id": None, "owner_id": -100, "date": now}, _post(-100, 5, 60, now)]
    got = _newest_unseen(posts, set(), 0, now)
    assert got["id"] == 5


# --------------------------------------------------------------------------- #
# Футер атрибуции и дедуп-cap
# --------------------------------------------------------------------------- #


def test_footer_with_name():
    assert _build_footer("SciTopus") == "\n\n🔭 SciTopus"


def test_footer_empty_when_no_name():
    assert _build_footer("") == ""
    assert _build_footer("  ") == ""


class _FakeWT:
    def __init__(self, lip=None):
        self.lip = lip or []


def test_mark_seen_dedup_and_cap():
    wt = _FakeWT(lip=[str(i) for i in range(LIP_HISTORY_MAX)])
    _mark_seen(wt, "new_lip")
    assert len(wt.lip) == LIP_HISTORY_MAX  # cap держится
    assert wt.lip[-1] == "new_lip"  # новый добавлен
    assert "0" not in wt.lip  # самый старый вытеснен


def test_mark_seen_no_duplicate():
    wt = _FakeWT(lip=["a", "b"])
    _mark_seen(wt, "b")
    assert wt.lip == ["a", "b"]  # повтор не добавляется


# --------------------------------------------------------------------------- #
# Регистрация beat-задачи (раз в день 20:00 MSK через parse_and_publish_theme)
# --------------------------------------------------------------------------- #


def test_krugozor_beat_registered():
    from tasks.celery_app import app

    assert "krugozor-broadcast-evening" in app.conf.beat_schedule
    entry = app.conf.beat_schedule["krugozor-broadcast-evening"]
    assert entry["task"] == "tasks.parsing_scheduler_tasks.parse_and_publish_theme"
    assert entry["kwargs"] == {"region_code": "copy", "theme": "krugozor"}
