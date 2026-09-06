"""Хвост поисковых фраз в описании: унификация не должна отбирать функцию.

06.09, унифицируя описания под шаблон v3, прогон остановили на первой же
группе. У Малмыжа под авторским текстом лежал список поисковых фраз («Новости
Малмыжа», «Происшествия в Малмыже», «Расписание автобусов», «Подслушано
Малмыж»…) — то, по чему сообщество находят во внутреннем поиске VK. Шаблон их
не содержал, значит «привести к единому виду» означало **потерять находимость**,
то есть сделать обратное цели ребрендинга. Малмыж откатили.

Урок шире описаний: **прежде чем заменять авторский артефакт шаблонным, надо
спросить, что этот артефакт делал.** Внешне он выглядел просто длинным текстом.
"""

import pytest

from modules.promotion.copy import render_group_description, render_search_tail


class TestSearchTail:
    def test_topics_are_bound_to_the_city(self):
        tail = render_search_tail(city="Малмыж")
        assert tail.startswith("Малмыж — новости,")
        assert "происшествия" in tail
        assert "расписание автобусов" in tail
        assert "подслушано" in tail

    @pytest.mark.parametrize("city", ["Малмыж", "Тужа", "Нема", "Балтаси", "Уржум"])
    def test_no_phrase_needs_a_declined_toponym(self, city):
        """Падеж кодом не выводится — формулировка обязана его не требовать.

        «Новости {город}» давало «Новости Малмыж», «Новости Тужа». Склонение
        русских топонимов без словаря невозможно (Малмыж→Малмыжа, Тужа→Тужи,
        Нема→Немы, Балтаси→Балтасей), а ошибка уезжает в публичное описание.
        """
        tail = render_search_tail(city=city)
        assert f"Новости {city}" not in tail
        assert tail.startswith(f"{city} — ")

    def test_localities_are_the_long_tail(self):
        tail = render_search_tail(city="Тужа", localities=["Азансола", "Артеково"])
        assert "Населённые пункты: Азансола, Артеково." in tail

    def test_localities_are_capped(self):
        """У Уржума их 119 — весь список читался бы как спам."""
        tail = render_search_tail(city="Уржум", localities=[f"Село{i}" for i in range(50)])
        assert tail.count(",") < 20
        assert "Село8" not in tail

    def test_no_localities_line_when_there_are_none(self):
        """У mi и bal локалитетов ноль — пустая строка-заголовок не нужна."""
        tail = render_search_tail(city="Малмыж", localities=[])
        assert "Населённые пункты" not in tail

    def test_blank_city_yields_no_tail(self):
        assert render_search_tail(city="  ") == ""

    @pytest.mark.parametrize("junk", [["", "  ", None]])
    def test_empty_localities_are_dropped(self, junk):
        tail = render_search_tail(city="Немы", localities=junk)
        assert "Населённые пункты" not in tail


class TestDescriptionKeepsBothHalves:
    def test_human_part_and_search_part_are_both_present(self):
        text = render_group_description(
            district_name="Малмыжского района",
            center_city="Малмыж",
            site_url="https://example.test/list",
            localities=["Калинино", "Савали"],
        )
        # Для человека
        assert "Новости, объявления и афиша Малмыжского района." in text
        assert "Прислать новость или объявление" in text
        # Для поиска
        assert "происшествия" in text
        assert "Калинино" in text

    def test_description_without_localities_still_has_topics(self):
        text = render_group_description(district_name="Балтасей", center_city="Балтаси")
        assert "Балтаси — новости," in text
        assert "Населённые пункты" not in text
