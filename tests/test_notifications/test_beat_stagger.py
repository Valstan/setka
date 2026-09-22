"""Тройка часовых проверок уведомлений разнесена по минутам — это гейт.

Предложка, сообщения и комментарии стояли подряд в :15/:16/:17. Каждая из них
одной задачей обходит ВСЕ регионы, ядро на боксе одно (concurrency 1), поэтому
подряд стоящие минуты означали не «три быстрых проверки», а очередь из трёх
тяжёлых обходов в одном дочернем процессе — ровно в окне OOM-убийств HH:17–19
(P164). Расписание — единственное место, где это лечится, и сблизить минуты
обратно можно одним символом, не заметив.

Тест сознательно проверяет РАССТОЯНИЕ, а не конкретные числа: минуты можно
двигать, нельзя — схлопывать.
"""

from __future__ import annotations

TRIO = (
    "check-suggested-hourly",
    "check-unread-messages-hourly",
    "check-recent-comments-hourly",
)

MIN_GAP_MINUTES = 5


def _minutes(entry) -> set:
    return {int(m) for m in entry["schedule"].minute}


def _circular_gap(a: int, b: int) -> int:
    """Расстояние по циферблату: между :57 и :15 восемнадцать минут, не сорок две."""
    d = abs(a - b) % 60
    return min(d, 60 - d)


def _trio_minutes() -> dict:
    from tasks.celery_app import app

    out = {}
    for key in TRIO:
        assert key in app.conf.beat_schedule, f"пропала beat-запись {key}"
        mins = _minutes(app.conf.beat_schedule[key])
        assert len(mins) == 1, f"{key}: ожидалась одна минута в часе, получено {sorted(mins)}"
        out[key] = mins.pop()
    return out


def test_trio_minutes_are_pairwise_far_apart():
    mins = _trio_minutes()
    keys = list(mins)
    for i, a in enumerate(keys):
        for b in keys[i + 1 :]:
            gap = _circular_gap(mins[a], mins[b])
            assert gap >= MIN_GAP_MINUTES, (
                f"{a} (:{mins[a]:02d}) и {b} (:{mins[b]:02d}) стоят в "
                f"{gap} мин друг от друга — на одном ядре это очередь, а не расписание"
            )


def test_trio_does_not_share_a_minute_with_the_parsing_waves():
    """Волны публикации сами по себе тяжёлые и тоже ветвятся по всем регионам."""
    from tasks.celery_app import app

    wave_minutes = set()
    for key, entry in app.conf.beat_schedule.items():
        if key.startswith("postopus-"):
            wave_minutes |= _minutes(entry)

    assert wave_minutes, "не нашлось ни одной волны postopus-* — тест потерял предмет"

    for key, minute in _trio_minutes().items():
        assert (
            minute not in wave_minutes
        ), f"{key} стоит на :{minute:02d} вместе с волнами публикации"


# Чужие обходы «по всем регионам за один проход». Разводить тройку нужно и от
# них: ядро одно, и очередь одинаково вырастает хоть от своих, хоть от чужих.
# `scan-suggested-ads` стоит на :25/:55 именно как офсет от этих проверок — его
# докстринг так и говорит; поставить проверку вплотную значит отменить чужое
# решение, не заметив его.
OTHER_SWEEPS = ("scan-suggested-ads", "scan-inbound-dm-ads")

MIN_SWEEP_CLEARANCE = 2


def test_trio_keeps_clearance_from_other_all_region_sweeps():
    from tasks.celery_app import app

    sweep_minutes = set()
    for key, entry in app.conf.beat_schedule.items():
        if key.startswith("postopus-") or key in OTHER_SWEEPS:
            sweep_minutes |= _minutes(entry)

    for name in OTHER_SWEEPS:
        assert name in app.conf.beat_schedule, f"пропала beat-запись {name} — тест потерял предмет"

    for key, minute in _trio_minutes().items():
        nearest = min(_circular_gap(minute, s) for s in sweep_minutes)
        assert nearest >= MIN_SWEEP_CLEARANCE, (
            f"{key} стоит на :{minute:02d}, в {nearest} мин от чужого обхода по всем "
            f"регионам — на одном ядре это очередь, а не расписание"
        )


def test_every_check_sends_the_aggregated_alert():
    """Раз «последней в цепочке» больше нет, алёрт обязан звать каждая.

    Иначе находка предложки на :15 ждала бы оповещения до :57 — сорок минут
    молчания там, где раньше было две минуты.
    """
    import inspect

    from tasks import celery_app as mod

    for name in ("check_suggested_posts", "check_unread_messages", "check_recent_comments"):
        src = inspect.getsource(getattr(mod, name))
        assert (
            "_maybe_send_telegram_notifications_alert()" in src
        ), f"{name} не зовёт агрегированный Telegram-алёрт"


def test_checks_do_not_return_the_whole_notification_list():
    """Список уехал бы в result backend целиком — до тысяч словарей на задачу.

    Читателя у него нет: UI берёт уведомления из Redis, а `web/api/notifications`
    дёргает задачу через `.delay()` и результата не ждёт.
    """
    import inspect

    from tasks import celery_app as mod

    for name in ("check_suggested_posts", "check_unread_messages", "check_recent_comments"):
        src = inspect.getsource(getattr(mod, name))
        assert (
            '"notifications": notifications' not in src
        ), f"{name} снова кладёт полный список уведомлений в результат задачи"
