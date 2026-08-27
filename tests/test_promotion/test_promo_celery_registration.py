"""Разводка модуля в Celery.

Гейт против самой дорогой ошибки этого класса: имя задачи попало в beat, а модуль
забыли добавить в ``include`` — тогда beat исправно шлёт, а worker молча не знает
такой задачи, и модуль «работает», ничего не делая.
"""


def test_module_is_in_celery_include():
    from tasks.celery_app import app

    assert "tasks.promo_tasks" in app.conf.include


def test_sync_task_registered():
    from tasks.celery_app import app
    from tasks.promo_tasks import sync_promo_enrollments  # noqa: F401

    assert "tasks.promo_tasks.sync_promo_enrollments" in app.tasks


def test_members_task_registered():
    from tasks.celery_app import app
    from tasks.promo_tasks import refresh_promo_community_members  # noqa: F401

    assert "tasks.promo_tasks.refresh_promo_community_members" in app.tasks


def test_dispatch_and_watchdog_registered():
    from tasks.celery_app import app
    from tasks.promo_tasks import check_promo_heartbeat, dispatch_promo  # noqa: F401

    assert "tasks.promo_tasks.dispatch_promo" in app.tasks
    assert "tasks.promo_tasks.check_promo_heartbeat" in app.tasks


def test_beat_entries_point_to_real_tasks():
    from tasks.celery_app import app

    schedule = app.conf.beat_schedule
    expected = {
        "promo-sync-enrollments": "tasks.promo_tasks.sync_promo_enrollments",
        "promo-members-refresh-weekly": "tasks.promo_tasks.refresh_promo_community_members",
        "promo-dispatch": "tasks.promo_tasks.dispatch_promo",
        "promo-watchdog": "tasks.promo_tasks.check_promo_heartbeat",
    }
    for key, task_name in expected.items():
        assert key in schedule, key
        assert schedule[key]["task"] == task_name


def test_dispatch_runs_only_in_daytime_window():
    """Ночной постинг — сигнал «бот» и для читателя, и для антиспама."""
    from tasks.celery_app import app

    hours = app.conf.beat_schedule["promo-dispatch"]["schedule"].hour
    assert min(hours) >= 9
    assert max(hours) <= 21


def test_dispatch_minute_does_not_collide_with_bulletin_slots():
    """Минута :08 свободна — два поста подряд на стене донора невозможны."""
    from tasks.celery_app import app

    busy = {0, 5, 7, 10, 12, 15, 16, 17, 20, 22, 25, 30, 35, 37, 40, 45, 47, 50, 55}
    minute = min(app.conf.beat_schedule["promo-dispatch"]["schedule"].minute)
    assert minute not in busy


def test_beat_entries_expire_and_do_not_catch_up():
    from tasks.celery_app import app

    for key in (
        "promo-sync-enrollments",
        "promo-members-refresh-weekly",
        "promo-dispatch",
        "promo-watchdog",
    ):
        options = app.conf.beat_schedule[key]["options"]
        assert options["expires"] > 0
        assert options["catchup"] is False


def test_enrollment_runs_after_member_snapshots():
    """Состав считается по свежему снимку, а не по вчерашнему.

    Снимок подписчиков собирается в 04:00; зачисление обязано быть позже в тот же
    час, иначе новый район сутки числится «без данных».
    """
    from tasks.celery_app import app

    snapshots = app.conf.beat_schedule["collect-member-snapshots-daily"]["schedule"]
    enrollments = app.conf.beat_schedule["promo-sync-enrollments"]["schedule"]

    assert snapshots.hour == enrollments.hour
    assert min(snapshots.minute) < min(enrollments.minute)
