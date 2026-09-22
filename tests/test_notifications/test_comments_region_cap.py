"""Шумный район не должен съедать общий лимит обхода комментариев.

Общий кап ``max_total_comments`` обрывает обход ЦЕЛИКОМ и делает это в порядке
перебора районов. То есть один район с виральной веткой мог выбрать все 5000 и
оставить остальные сорок с лишним непрочитанными — а в логе это выглядело как
«дошли до капа», не как «потеряли сорок районов». Региональный кап отрезает
хвост шумного и пускает обход дальше.

Отдельно проверяется, что при срабатывании ОБЩЕГО капа уже собранное по
текущему району не теряется: оно копится в локальном списке, и забыть его
слить — ровно та ошибка, которую этот тест ловит.
"""

from __future__ import annotations

from collections import Counter
from unittest.mock import MagicMock, patch

from modules.notifications.vk_comments_checker import VKCommentsChecker

CUTOFF = 1716200000
LATER = CUTOFF + 1000

NOISY = -111
QUIET = -222


def _make_checker():
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="user-api")
        m.return_value = instance
        return VKCommentsChecker("user-token", community_tokens={})


def _groups():
    return [
        {
            "vk_group_id": NOISY,
            "region_id": 1,
            "region_code": "noisy",
            "region_name": "Шумный ИНФО",
        },
        {
            "vk_group_id": QUIET,
            "region_id": 2,
            "region_code": "quiet",
            "region_name": "Тихий ИНФО",
        },
    ]


def _wire(checker, *, noisy_count, quiet_count):
    """Один пост на район; у шумного — noisy_count комментариев."""

    def _posts(owner_id, cutoff_ts, count):
        return [{"id": 1}]

    def _comments(owner_id, post_id, cutoff_ts):
        n = noisy_count if owner_id == NOISY else quiet_count
        return [{"id": i, "date": LATER, "text": f"c{i}"} for i in range(n)]

    checker._get_recent_wall_posts_with_comments = _posts
    checker.check_post_comments_since = _comments


async def test_noisy_region_is_trimmed_and_the_quiet_one_still_read():
    checker = _make_checker()
    _wire(checker, noisy_count=600, quiet_count=10)

    out = await checker.check_recent_comments_for_region_groups(
        region_groups=_groups(),
        cutoff_ts=CUTOFF,
        max_comments_per_region=500,
    )

    by_region = Counter(n["region_code"] for n in out)
    assert by_region["noisy"] == 500, "хвост шумного района обязан быть отрезан"
    assert by_region["quiet"] == 10, "тихий район не должен пострадать от соседа"


async def test_region_under_the_cap_is_untouched():
    checker = _make_checker()
    _wire(checker, noisy_count=3, quiet_count=4)

    out = await checker.check_recent_comments_for_region_groups(
        region_groups=_groups(),
        cutoff_ts=CUTOFF,
        max_comments_per_region=500,
    )

    assert len(out) == 7


async def test_total_cap_keeps_what_the_current_region_already_gave():
    """Общий кап не должен терять накопленное по текущему району."""
    checker = _make_checker()
    _wire(checker, noisy_count=10, quiet_count=10)

    out = await checker.check_recent_comments_for_region_groups(
        region_groups=_groups(),
        cutoff_ts=CUTOFF,
        max_total_comments=5,
        max_comments_per_region=500,
    )

    assert len(out) == 5


async def test_region_cap_warning_names_the_district(caplog):
    """Обрезка обязана назвать район: иначе потеря данных молчалива."""
    checker = _make_checker()
    _wire(checker, noisy_count=600, quiet_count=1)

    with caplog.at_level("WARNING"):
        await checker.check_recent_comments_for_region_groups(
            region_groups=_groups(),
            cutoff_ts=CUTOFF,
            max_comments_per_region=500,
        )

    assert any("noisy" in r.getMessage() for r in caplog.records)
