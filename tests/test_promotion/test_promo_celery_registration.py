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


def test_beat_entries_point_to_real_tasks():
    from tasks.celery_app import app

    schedule = app.conf.beat_schedule
    assert "promo-sync-enrollments" in schedule
    assert "promo-members-refresh-weekly" in schedule

    assert schedule["promo-sync-enrollments"]["task"] == "tasks.promo_tasks.sync_promo_enrollments"
    assert (
        schedule["promo-members-refresh-weekly"]["task"]
        == "tasks.promo_tasks.refresh_promo_community_members"
    )


def test_beat_entries_expire_and_do_not_catch_up():
    from tasks.celery_app import app

    for key in ("promo-sync-enrollments", "promo-members-refresh-weekly"):
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
