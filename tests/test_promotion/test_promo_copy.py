"""Тексты раскрутки.

Проверяем не красоту, а два свойства, за которые платит прод: пост не должен
читаться антиспамом VK как реклама (код 219 ставит метку на стену донора), и
футер не должен выталкивать новость из сводки, упирающейся в 4096 символов.
"""

from modules.promotion.copy import (
    FOOTER_MAX_LENGTH,
    render_footer_line,
    render_group_description,
    render_oblast_digest,
    render_outreach_draft,
    render_promo_post,
    render_welcome_post,
)

# Слова и приёмы, по которым текст читается как реклама, а не как сообщение соседям.
AD_MARKERS = (
    "подпишись",
    "подписывайтесь",
    "скорее",
    "успей",
    "только сегодня",
    "жми",
    "переходи",
    "!!!",
)


def assert_not_ad_like(text: str):
    lowered = text.lower()
    for marker in AD_MARKERS:
        assert marker not in lowered, f"текст читается как реклама: {marker!r}"
    assert text.count("!") <= 1


class TestPromoPost:
    def test_mentions_neighbour_on_first_hop(self):
        text = render_promo_post(target_name="Суна", target_url="https://vk.com/suna_info43", hop=1)
        assert "сосед" in text.lower()
        assert "https://vk.com/suna_info43" in text
        assert_not_ad_like(text)

    def test_does_not_claim_neighbourhood_on_second_hop(self):
        # На втором хопе «сосед» — уже неправда, и местный читатель это видит.
        text = render_promo_post(
            target_name="Кумёны", target_url="https://vk.com/kumyony_info43", hop=2
        )
        assert "у соседей" not in text.lower()
        assert "сети" in text.lower()
        assert_not_ad_like(text)

    def test_url_present_because_copyright_is_dropped_by_vk(self):
        # Параметр copyright VK молча отбрасывает для vk.com-ссылок (G64),
        # поэтому ссылка обязана жить в теле поста.
        text = render_promo_post(target_name="Зуевка", target_url="https://vk.com/zuevka_info43")
        assert "https://vk.com/zuevka_info43" in text

    def test_optional_hint_is_included(self):
        text = render_promo_post(
            target_name="Суна",
            target_url="https://vk.com/suna_info43",
            district_hint="Оттуда возят молоко на наш рынок.",
        )
        assert "молоко" in text

    def test_no_double_blank_lines(self):
        text = render_promo_post(target_name="Суна", target_url="https://vk.com/x")
        assert "\n\n\n" not in text


class TestFooter:
    def test_renders_neighbours(self):
        line = render_footer_line([{"name": "Суна", "url": "https://vk.com/suna_info43"}])
        assert "Суна" in line
        assert len(line) <= FOOTER_MAX_LENGTH

    def test_drops_items_until_it_fits(self):
        many = [
            {"name": "Район" + str(i), "url": "https://vk.com/very_long_name_" + str(i)}
            for i in range(5)
        ]
        line = render_footer_line(many)
        assert len(line) <= FOOTER_MAX_LENGTH

    def test_empty_when_nothing_to_show(self):
        assert render_footer_line([]) == ""
        assert render_footer_line([{"name": "Суна"}]) == ""

    def test_empty_is_a_valid_outcome_for_oversized_names(self):
        # Лучше сводка без подписи, чем сводка с обрезанной новостью.
        huge = [{"name": "х" * 200, "url": "https://vk.com/" + "y" * 200}]
        assert render_footer_line(huge) == ""


class TestGroupSetupTexts:
    def test_description_mentions_district_and_center(self):
        text = render_group_description(district_name="Сунского района", center_city="Суна")
        assert "Сунского района" in text
        assert "Суна" in text
        assert_not_ad_like(text)

    def test_description_without_center_has_no_dangling_sentence(self):
        text = render_group_description(district_name="Сунского района")
        assert "Центр" not in text

    def test_welcome_post_lists_neighbours(self):
        text = render_welcome_post(
            district_name="Сунского района",
            neighbors=[{"name": "Нолинск ИНФО", "url": "https://vk.com/nolinsk"}],
        )
        assert "Нолинск ИНФО" in text
        assert "https://vk.com/nolinsk" in text
        assert_not_ad_like(text)

    def test_welcome_post_works_without_neighbours(self):
        text = render_welcome_post(district_name="Сунского района")
        assert "Ленты соседних районов" not in text


class TestOblastDigest:
    def test_lists_all_targets_in_one_post(self):
        # Один пост на несколько районов, иначе очередь из девятнадцати районов
        # при квоте «один промо в неделю» растянулась бы на девятнадцать недель.
        text = render_oblast_digest(
            [
                {"name": "Суна", "url": "https://vk.com/a"},
                {"name": "Кумёны", "url": "https://vk.com/b"},
                {"name": "Зуевка", "url": "https://vk.com/c"},
            ]
        )
        assert "Суна" in text and "Кумёны" in text and "Зуевка" in text
        assert_not_ad_like(text)

    def test_empty_without_targets(self):
        assert render_oblast_digest([]) == ""


class TestOutreachDraft:
    def test_is_written_in_first_person_for_manual_sending(self):
        text = render_outreach_draft(
            group_name="Подслушано Суна",
            district_name="Сунского района",
            target_url="https://vk.com/suna_info43",
            author_name="Валентин",
        )
        assert "Подслушано Суна" in text
        assert "https://vk.com/suna_info43" in text
        assert "Валентин" in text
        assert_not_ad_like(text)

    def test_offers_something_in_return(self):
        text = render_outreach_draft(
            group_name="Подслушано Суна",
            district_name="Сунского района",
            target_url="https://vk.com/suna_info43",
        )
        assert "ссылку на вас" in text
