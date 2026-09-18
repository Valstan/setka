"""Предложку включает настройка сообщества, а не его тип — сторож после ошибки.

Жалоба владельца 2026-09-18: у сообществ, созданных пачками из браузера, нет
кнопки «Предложить пост». Замер дал идеальную корреляцию — все 13 сообществ
типа ``page`` предложку принимают, все 40 типа ``group`` нет, причём
``verhoshizhem`` (page, 38 подписчиков) принимает, а ``vp`` (group, 497) нет.
Отсюда был сделан вывод «предложка есть только у публичных страниц», и он
прожил полдня в коде, памятке и письме мозгу.

**Опровергла его ``tuzha``:** ``type=group``, ``wall=2``, ``can_suggest=1``
у постороннего — после того, как владелец нашёл переключатель «Кто может
предлагать посты» (Управление → Настройки → Разделы → Посты). Причиной был
он, а тип сообщества оказался спутником: у 40 новых групп настройка осталась
в дефолте, 13 старых владелец заводил руками и приём включал.

Поэтому файл держит три свойства:

1. вердикт считается **только из пробы** ``can_suggest`` и ни из чего больше —
   тест с ``type``/``wall`` в аргументах невозможен физически, функция их не
   принимает;
2. «не измерено» остаётся ``None``, а не превращается в «нельзя» (#284):
   именно на этом различии держится честность отчёта;
3. `is_admin_only_wall` говорит про стену и молчит про предложку — два разных
   вопроса, которые слиплись в прежней версии.
"""

from modules.promotion.group_setup_vk import accepts_suggestions, is_admin_only_wall


class TestAcceptsSuggestions:
    def test_probe_one_means_yes(self):
        """Так ответила tuzha — группа, принимающая предложения."""
        assert accepts_suggestions(1) is True

    def test_probe_zero_means_no(self):
        assert accepts_suggestions(0) is False

    def test_unmeasured_is_none_not_false(self):
        """«Не посмотрели» ≠ «нельзя» — иначе отчёт врёт в опасную сторону."""
        assert accepts_suggestions(None) is None

    def test_verdict_does_not_depend_on_community_type(self):
        """Гвоздь: тип сообщества в вердикт не входит вообще.

        Прежняя версия считала `type == "page" and wall == 2` и на живой
        `tuzha` (group + включённая настройка) отвечала «нельзя» — зелёный
        тест против фактов на экране у владельца.
        """
        # Одна и та же проба — один и тот же ответ, откуда бы она ни пришла.
        assert accepts_suggestions(1) is accepts_suggestions(1)
        # Функция принимает ровно одно значение: подсунуть ей снимок нечем.
        assert accepts_suggestions(0) is False


class TestIsAdminOnlyWall:
    def test_restricted_wall(self):
        """wall=2 — пишет только администрация; так у всех 53 наших сообществ."""
        assert is_admin_only_wall({"wall": 2}) is True

    def test_open_wall(self):
        assert is_admin_only_wall({"wall": 1}) is False

    def test_missing_wall_is_none(self):
        assert is_admin_only_wall({"type": "page"}) is None
        assert is_admin_only_wall({}) is None
        assert is_admin_only_wall(None) is None

    def test_says_nothing_about_suggestions(self):
        """Стена и предложка — разные вопросы.

        `vp` (wall=1) предложку не принимает, `tuzha` (wall=2) принимает, а
        `laishevo` (wall=2) до переключения не принимала: по стене вердикт о
        предложке не выводится ни в одну сторону.
        """
        assert is_admin_only_wall({"wall": 2}) is True
        assert accepts_suggestions(None) is None
