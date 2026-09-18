"""Предложка есть только у публичной страницы — сторож дискриминатора.

Жалоба владельца 2026-09-18: у сообществ, созданных пачками из браузера, нет
кнопки «Предложить пост». Три правдоподобных объяснения проверялись живым
замером, а не чтением доки:

1. «не набралось подписчиков» — **опровергнуто**: ``verhoshizhem`` (тип ``page``,
   38 подписчиков) заявки из предложки имеет, ``vp`` (тип ``group``, 497) — ноль;
2. «что-то в настройках стены» — **опровергнуто**: ``wall=2`` (ограниченная) у
   всех 53, и у работающих, и у молчащих;
3. **тип сообщества** — подтверждено: ``page`` + wall=2 → посторонний видит
   ``can_suggest=1``; ``group`` + wall=2 → ``can_post=0`` и ``can_suggest=0``,
   то есть прислать новость нечем вовсе.

Гвоздь этого файла — последний тест: «не измерено» не имеет права выглядеть как
«нельзя». Снимок без ``type``/``wall`` даёт ``None``, иначе первое же сообщество
с нечитаемым снимком попадёт в отчёт как сломанное (#284).
"""

from modules.promotion.group_setup_vk import accepts_suggestions


class TestAcceptsSuggestions:
    def test_public_page_with_restricted_wall_accepts(self):
        """Эталон: так выглядят все 13 сообществ, куда люди годами предлагают."""
        assert accepts_suggestions({"type": "page", "wall": 2}) is True

    def test_group_with_restricted_wall_does_not_accept(self):
        """Тип «группа» — предложки нет как функции, стена тут ни при чём."""
        assert accepts_suggestions({"type": "group", "wall": 2}) is False

    def test_group_with_open_wall_does_not_accept(self):
        """Открытая стена — это не предложка: посторонний пишет сразу в ленту."""
        assert accepts_suggestions({"type": "group", "wall": 1}) is False

    def test_page_with_open_wall_does_not_accept(self):
        """У паблика предложка живёт ровно на «ограниченной» стене."""
        assert accepts_suggestions({"type": "page", "wall": 1}) is False

    def test_page_with_wall_off_does_not_accept(self):
        assert accepts_suggestions({"type": "page", "wall": 0}) is False

    def test_unmeasured_snapshot_is_none_not_false(self):
        """«Не посмотрели» ≠ «нельзя» — иначе аудит соврёт в опасную сторону."""
        assert accepts_suggestions({"type": "page"}) is None
        assert accepts_suggestions({"wall": 2}) is None
        assert accepts_suggestions({}) is None
        assert accepts_suggestions(None) is None
