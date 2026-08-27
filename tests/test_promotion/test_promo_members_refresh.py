"""Обновление размеров донорских сообществ.

Главное здесь — цена: вся сеть должна закрываться четырьмя вызовами. Если
батчинг сломается и на каждую группу пойдёт свой запрос, это 1627 вызовов
вместо четырёх, и модуль тихо съест дневной бюджет токена.
"""

from modules.promotion.members_refresh import VK_BATCH, index_members, plan_batches


class TestPlanBatches:
    def test_whole_network_fits_in_four_calls(self):
        ids = list(range(1, 1628))  # столько сообществ на проде
        assert len(plan_batches(ids)) == 4

    def test_batch_size_matches_vk_limit(self):
        batches = plan_batches(list(range(1, 1201)))
        assert all(len(b) <= VK_BATCH for b in batches)
        assert len(batches[0]) == VK_BATCH

    def test_negative_owner_ids_are_normalised(self):
        assert plan_batches([-158787639]) == [[158787639]]

    def test_duplicates_collapse(self):
        # Одна группа может лежать в communities несколько раз под разными
        # категориями — спрашивать VK о ней дважды незачем.
        assert plan_batches([-5, 5, 5]) == [[5]]

    def test_empty_input_gives_no_calls(self):
        assert plan_batches([]) == []

    def test_zero_is_dropped(self):
        assert plan_batches([0, 7]) == [[7]]


class TestIndexMembers:
    def test_maps_id_to_count(self):
        items = [{"id": 5, "members_count": 120}, {"id": 6, "members_count": 0}]
        assert index_members(items) == {5: 120, 6: 0}

    def test_missing_count_becomes_none_not_zero(self):
        # «Спрашивали, не ответили» и «пустая группа» — разные вещи, и в БД они
        # обязаны отличаться: NULL против 0.
        assert index_members([{"id": 5}]) == {5: None}

    def test_ignores_garbage_rows(self):
        assert index_members([None, "x", {}, {"id": 5, "members_count": 1}]) == {5: 1}

    def test_none_input_is_empty(self):
        assert index_members(None) == {}
