"""Подбор пары «донор → цель».

Данные в тестах взяты с прода (замер 28.08.2026): у Суны сильные соседи есть
(Нолинск 2560, Нема 1800), а у Кумён и Зуевки — нет ни одного, и добраться до
сильного они могут только вторым хопом. Это не выдуманный крайний случай, а
основная ситуация: сильный сосед есть лишь у 4 районов из 24.
"""

from modules.promotion.pairing import (
    DonorCandidate,
    TargetCandidate,
    neighbors_at_hop,
    plan_pairs,
    symmetrize,
)


def donor(code, members, *, token=True, given=0, region_id=None, group_id=None):
    return DonorCandidate(
        region_id=region_id if region_id is not None else abs(hash(code)) % 1000,
        code=code,
        group_id=group_id if group_id is not None else -abs(hash(code)) % 100000 - 1,
        members=members,
        has_community_token=token,
        given_last_30d=given,
    )


def target(code, members, *, region_id=None, group_id=None, received=0):
    return TargetCandidate(
        region_id=region_id if region_id is not None else abs(hash(code)) % 1000 + 5000,
        code=code,
        group_id=group_id if group_id is not None else -abs(hash(code)) % 100000 - 90001,
        members=members,
        received_total=received,
    )


# Кусок настоящего графа соседства.
GRAPH = {
    "suna": ["bogorodskoe", "kumyony", "nema", "nolinsk", "verhoshizhem", "zuevka"],
    "kumyony": ["chepetsk", "orichi", "slobodskoy", "suna", "verhoshizhem", "zuevka"],
    "zuevka": ["belholunitsa", "chepetsk", "kumyony", "slobodskoy", "suna"],
    "nolinsk": ["klz", "leb", "mi", "nema", "sovetsk", "suna", "verhoshizhem"],
    "nema": ["klz", "leb", "mi", "nolinsk", "suna", "verhoshizhem"],
    "verhoshizhem": ["klz", "kumyony", "leb", "mi", "nolinsk", "sovetsk", "suna"],
    "chepetsk": ["kumyony", "slobodskoy", "zuevka"],
    "slobodskoy": ["chepetsk", "kumyony", "zuevka"],
    "belholunitsa": ["zuevka"],
    "bogorodskoe": ["suna"],
    "orichi": ["kumyony"],
}


class TestSymmetrize:
    def test_edge_written_once_works_both_ways(self):
        sym = symmetrize({"a": ["b"], "b": []})
        assert "a" in sym["b"] and "b" in sym["a"]

    def test_self_loop_dropped(self):
        assert symmetrize({"a": ["a"]})["a"] == set()

    def test_real_graph_is_already_symmetric(self):
        # На текущих данных симметризация ничего не меняет — но соседство
        # физически двустороннее, и полагаться на аккуратность ручного CSV незачем.
        sym = symmetrize(GRAPH)
        for code, neighbours in sym.items():
            for other in neighbours:
                assert code in sym[other]


class TestHops:
    def test_first_hop_is_direct_neighbours(self):
        assert "nolinsk" in neighbors_at_hop(symmetrize(GRAPH), "suna", 1)

    def test_second_hop_excludes_first(self):
        sym = symmetrize(GRAPH)
        first = neighbors_at_hop(sym, "kumyony", 1)
        second = neighbors_at_hop(sym, "kumyony", 2)
        assert not (first & second)
        assert "nolinsk" in second  # через Суну или Верхошижемье

    def test_start_node_never_returned(self):
        sym = symmetrize(GRAPH)
        assert "suna" not in neighbors_at_hop(sym, "suna", 2)


class TestPlanPairs:
    def test_direct_strong_neighbour_wins(self):
        pairs, orphans = plan_pairs(
            [target("suna", 0)],
            [donor("nolinsk", 2560), donor("nema", 1800)],
            GRAPH,
        )
        assert len(pairs) == 1
        assert pairs[0].donor.code == "nolinsk"  # крупнее при прочих равных
        assert pairs[0].hop == 1
        assert orphans == []

    def test_second_hop_saves_northern_cluster(self):
        pairs, orphans = plan_pairs(
            [target("kumyony", 0)],
            [donor("nolinsk", 2560)],
            GRAPH,
        )
        assert len(pairs) == 1
        assert pairs[0].hop == 2
        assert orphans == []

    def test_second_hop_can_be_disabled(self):
        pairs, orphans = plan_pairs(
            [target("kumyony", 0)],
            [donor("nolinsk", 2560)],
            GRAPH,
            second_hop_enabled=False,
        )
        assert pairs == []
        assert len(orphans) == 1
        assert "нет сильного соседа" in orphans[0].reason

    def test_orphan_when_no_strong_donor_anywhere(self):
        pairs, orphans = plan_pairs([target("zuevka", 0)], [], GRAPH)
        assert pairs == []
        assert orphans[0].target.code == "zuevka"
        assert "нет сильного соседа" in orphans[0].reason

    def test_own_community_token_beats_bigger_audience(self):
        # Донор со своим ключом публикуется, не расходуя аккаунт, которым
        # публикуется вся сеть. Это важнее лишней тысячи подписчиков.
        pairs, _ = plan_pairs(
            [target("suna", 0)],
            [donor("nolinsk", 2560, token=False), donor("nema", 1800, token=True)],
            GRAPH,
        )
        assert pairs[0].donor.code == "nema"

    def test_closer_donor_beats_bigger_one(self):
        pairs, _ = plan_pairs(
            [target("suna", 0)],
            [donor("nema", 1800), donor("mi", 5307)],
            GRAPH,
        )
        assert pairs[0].donor.code == "nema"  # первый хоп против второго

    def test_donor_that_gave_recently_goes_last(self):
        pairs, _ = plan_pairs(
            [target("suna", 0)],
            [donor("nolinsk", 2000, given=3), donor("nema", 2000, given=0)],
            GRAPH,
        )
        assert pairs[0].donor.code == "nema"

    def test_weakest_target_served_first(self):
        pairs, _ = plan_pairs(
            [target("suna", 50), target("kumyony", 0)],
            [donor("nolinsk", 2560)],
            GRAPH,
            max_pairs=1,
        )
        assert pairs[0].target.code == "kumyony"

    def test_one_donor_serves_one_target_per_slot(self):
        pairs, orphans = plan_pairs(
            [target("suna", 0), target("kumyony", 1)],
            [donor("nolinsk", 2560)],
            GRAPH,
        )
        assert len(pairs) == 1
        assert len(orphans) == 1
        assert "уже отработали" in orphans[0].reason

    def test_busy_donor_is_skipped(self):
        strong = donor("nolinsk", 2560, group_id=-179306667)
        pairs, orphans = plan_pairs(
            [target("suna", 0)],
            [strong],
            GRAPH,
            busy_donor_group_ids=[-179306667],
        )
        assert pairs == []
        assert "уже отработали" in orphans[0].reason

    def test_busy_target_is_skipped_silently(self):
        weak = target("suna", 0, region_id=42)
        pairs, orphans = plan_pairs(
            [weak], [donor("nolinsk", 2560)], GRAPH, busy_target_region_ids=[42]
        )
        assert pairs == []
        assert orphans == []  # цель уже получила своё — это не сирота

    def test_max_pairs_caps_output(self):
        pairs, _ = plan_pairs(
            [target("suna", 0), target("kumyony", 1), target("zuevka", 2)],
            [donor("nolinsk", 2560), donor("nema", 1800), donor("mi", 5307)],
            GRAPH,
            max_pairs=2,
        )
        assert len(pairs) == 2

    def test_result_is_deterministic(self):
        args = (
            [target("suna", 0)],
            [donor("nolinsk", 2000), donor("nema", 2000)],
            GRAPH,
        )
        first, _ = plan_pairs(*args)
        second, _ = plan_pairs(*args)
        assert first[0].donor.code == second[0].donor.code
