"""Настройки Celery, поломка которых видна не сразу, а через сутки.

Файл целиком потребляется через ``app.config_from_object`` — здесь нет функций,
есть значения, и цена у них эксплуатационная. Держим ровно те, у которых уже был
инцидент или чья неверная величина не даёт ни ошибки, ни лога.
"""

from __future__ import annotations

from config import celery_config as cc


def test_child_is_recycled_often_enough_to_outrun_the_leak():
    """Порог переработки дочернего процесса — лечение OOM-убийств (2026-09-14).

    Замер на проде: 250–380 задач в час при одном ядре, то есть один дочерний
    процесс. Прежние 1000 задач = 3.3 часа жизни ребёнка, а ядро убивало его
    раньше — восемь раз за 09.09–14.09 на 390–518 МБ при 1536 МБ и swap = 0.

    Граница снизу здесь не меньше важна, чем сверху: слишком частая переработка
    превращает форк с ре-импортом приложения в заметную долю работы одного ядра.
    """
    assert 100 <= cc.worker_max_tasks_per_child <= 400


def test_late_ack_and_reject_on_worker_lost_go_together():
    """Обе настройки — одна пара, и порознь они меняют смысл.

    ``task_acks_late`` без ``task_reject_on_worker_lost`` означает, что задача,
    чей процесс убило ядро, останется неподтверждённой и повиснет; вместе они
    возвращают её в очередь. На боксе, где OOM-убийство — регулярное событие,
    это разница между «повторится» и «потеряется молча».
    """
    assert cc.task_acks_late is True
    assert cc.task_reject_on_worker_lost is True


def test_prefetch_is_one_so_a_killed_child_takes_one_task_with_it():
    """При prefetch > 1 убитый ребёнок уносит целую пачку зарезервированных задач."""
    assert cc.worker_prefetch_multiplier == 1


def test_memory_cap_sits_below_the_observed_kill_band():
    """Второй предохранитель к порогу по задачам (2026-09-22).

    Порог по числу задач отмеряет ВРЕМЯ жизни ребёнка, а ядро убивает по
    ПАМЯТИ: при той же тысяче задач в час всплеск на одной тяжёлой волне
    доводил RSS до 390–518 МБ (dmesg 09.09–14.09) раньше, чем наступала
    переработка. Потолок памяти переводит это в чистый форк после задачи.

    Границы: снизу свежий ребёнок ~87 МБ, и порог вплотную к нему заставил бы
    воркер перерождаться после каждой нормальной волны; сверху 390 МБ —
    наблюдаемая нижняя граница убийств, выше неё предохранитель не успевает.
    """
    assert 256 * 1024 <= cc.worker_max_memory_per_child <= 384 * 1024


def test_hard_time_limit_bounds_a_hung_task_without_cutting_normal_ones():
    """На одном ядре зависшая задача блокирует ВСЕ остальные, включая
    поминутные диспетчеры: concurrency 1 — это одна очередь на всё.

    Нижняя граница — замеренный максимум регулярных задач (368 с у волны):
    лимит короче рубил бы здоровую работу.
    """
    assert cc.task_time_limit >= 1800
    assert cc.task_time_limit <= 2 * 3600


def test_soft_time_limit_is_deliberately_absent():
    """Мягкий лимит здесь опаснее зависания, от которого он защищает.

    Задачи синхронные снаружи и асинхронные внутри: корутина крутится через
    ``utils.celery_asyncio.run_coro``, то есть ``loop.run_until_complete`` на
    ОДНОМ персистентном цикле процесса. ``SoftTimeLimitExceeded`` прилетает
    сигналом в тот же поток, выбрасывает управление из ``run_until_complete``,
    но корутину не отменяет — она остаётся на общем цикле и продолжает
    исполняться уже во время СЛЕДУЮЩЕЙ задачи, держа соединение из пула и
    досылая побочные действия. Жёсткий лимит убивает процесс вместе с циклом и
    такого не оставляет.

    Тест существует затем, что «добавить мягкий лимит рядом с жёстким» —
    первое, что приходит в голову правящему этот файл.
    """
    assert getattr(cc, "task_soft_time_limit", None) is None

    from tasks.celery_app import app

    assert app.conf["task_soft_time_limit"] is None


def test_the_longest_weekly_task_declares_its_own_limits():
    """Общий лимит рассчитан на регулярные задачи, а не на недельный обход.

    Замер 2026-09-22: `recheck_all_active_regions` идёт 1750 с — впятеро
    дольше второй по длительности задачи, и растёт вместе с сетью, потому что
    обходит все сообщества всех регионов через тормоз ВК. Под общим лимитом у
    неё было бы меньше двух раз запаса, и однажды недельная проверка начала бы
    обрываться раз в неделю в 04:00 — то есть незаметно.
    """
    # Модуль регистрирует задачи на импорте; `include=[...]` у приложения
    # Celery срабатывает только при старте воркера, не при обычном импорте.
    import tasks.discovery_tasks  # noqa: F401
    from tasks.celery_app import app

    task = app.tasks["tasks.discovery_tasks.recheck_all_active_regions"]

    assert task.time_limit is not None, "у самой долгой задачи нет своего лимита"
    assert task.time_limit > cc.task_time_limit
    assert task.time_limit >= 3 * 1750, "запас меньше трёхкратного от замеренных 1750 с"
    # Мягкого лимита нет и тут — причина та же, см. test_soft_time_limit_is_deliberately_absent.
    assert task.soft_time_limit is None


def test_the_app_actually_applies_these_values():
    """Файл значений и работающий воркер — разные вещи, и разница молчалива.

    Весь смысл этого модуля в том, что он потребляется целиком через
    ``app.config_from_object``. Опечатка в имени ключа не даёт ни ошибки, ни
    строки в логе: Celery просто не знает такой настройки, а тест на сам файл
    остаётся зелёным. Поэтому проверяем не константы, а то, что видит
    приложение.
    """
    from tasks.celery_app import app

    assert app.conf["worker_max_memory_per_child"] == cc.worker_max_memory_per_child
    assert app.conf["worker_max_tasks_per_child"] == cc.worker_max_tasks_per_child
    assert app.conf["task_time_limit"] == cc.task_time_limit
    assert app.conf["worker_prefetch_multiplier"] == cc.worker_prefetch_multiplier
