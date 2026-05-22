"""Tests for paginated comment collection in VKCommentsChecker.

Old behaviour: single wall.getComments(count=100) → silent loss of comments
beyond the first 100, full loss of thread.items (replies). New behaviour:
loop offset+=100 while the freshest comment of the page is still inside the
24h window; per-comment thread.items unpacked into the flat result.
"""

from unittest.mock import MagicMock, patch

from modules.notifications.vk_comments_checker import VKCommentsChecker

OWNER = -123456789
POST = 42
CUTOFF = 1716200000  # arbitrary unix ts; "now-24h"
LATER = CUTOFF + 1000


def _make_checker(no_community=True):
    with patch("modules.notifications.base_checker.vk_api.VkApi") as m:
        instance = MagicMock()
        instance.get_api.return_value = MagicMock(name="user-api")
        m.return_value = instance
        checker = VKCommentsChecker("user-token", community_tokens={})
    return checker


def _page(items, total):
    """VK response shape for wall.getComments."""
    return {"items": items, "count": total}


def _comment(id_, ts, text="ok", thread_items=None):
    c = {"id": id_, "date": ts, "text": text}
    if thread_items is not None:
        c["thread"] = {"items": thread_items}
    return c


def test_single_page_under_100_no_pagination():
    checker = _make_checker()
    checker.vk.wall.getComments.return_value = _page(
        [_comment(1, LATER), _comment(2, LATER)], total=2
    )

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    assert [c["id"] for c in result] == [1, 2]
    checker.vk.wall.getComments.assert_called_once()
    _, kwargs = checker.vk.wall.getComments.call_args
    assert kwargs["offset"] == 0
    assert kwargs["count"] == 100
    assert kwargs["thread_items"] == 1
    assert kwargs["extended"] == 1


def test_pagination_three_pages():
    """250 comments → 3 calls with offset 0, 100, 200."""
    checker = _make_checker()
    page_a = _page([_comment(i, LATER) for i in range(100)], total=250)
    page_b = _page([_comment(100 + i, LATER) for i in range(100)], total=250)
    page_c = _page([_comment(200 + i, LATER) for i in range(50)], total=250)
    checker.vk.wall.getComments.side_effect = [page_a, page_b, page_c]

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    assert len(result) == 250
    assert checker.vk.wall.getComments.call_count == 3
    offsets = [call.kwargs["offset"] for call in checker.vk.wall.getComments.call_args_list]
    assert offsets == [0, 100, 200]


def test_pagination_stops_when_all_older_than_cutoff():
    """If a whole page is older than cutoff, don't continue paging."""
    checker = _make_checker()
    page_a = _page([_comment(i, LATER) for i in range(100)], total=300)
    # Second page: all dated BEFORE cutoff — they're outside the 24h window
    page_b = _page([_comment(100 + i, CUTOFF - 999) for i in range(100)], total=300)
    page_c = _page([_comment(200 + i, LATER) for i in range(100)], total=300)

    checker.vk.wall.getComments.side_effect = [page_a, page_b, page_c]

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    # 100 from page A; page B all out-of-window → stop, don't fetch C.
    assert len(result) == 100
    assert checker.vk.wall.getComments.call_count == 2


def test_thread_items_flattened():
    """Each top-level comment with thread.items expands into multiple entries."""
    checker = _make_checker()
    checker.vk.wall.getComments.return_value = _page(
        [
            _comment(
                1,
                LATER,
                text="root",
                thread_items=[
                    _comment(2, LATER + 1, text="reply 1"),
                    _comment(3, LATER + 2, text="reply 2"),
                ],
            ),
            _comment(4, LATER, text="another root"),
        ],
        total=2,
    )

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    ids = [c["id"] for c in result]
    assert ids == [1, 2, 3, 4]
    # Reply markers
    reply1 = next(c for c in result if c["id"] == 2)
    assert reply1.get("is_reply") is True
    assert reply1.get("parent_id") == 1
    root = next(c for c in result if c["id"] == 1)
    assert root.get("is_reply") is not True


def test_thread_items_outside_cutoff_skipped():
    """Replies older than cutoff are skipped even if the parent passed."""
    checker = _make_checker()
    checker.vk.wall.getComments.return_value = _page(
        [
            _comment(
                1,
                LATER,
                text="parent in window",
                thread_items=[
                    _comment(2, LATER + 1, text="reply in window"),
                    _comment(3, CUTOFF - 999, text="reply old"),
                ],
            ),
        ],
        total=1,
    )

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    ids = [c["id"] for c in result]
    assert ids == [1, 2]  # 3 dropped


def test_pagination_terminates_when_total_reached():
    """If response.count says total=50 and we already pulled 100 → stop (sanity)."""
    checker = _make_checker()
    page_a = _page([_comment(i, LATER) for i in range(50)], total=50)
    checker.vk.wall.getComments.return_value = page_a

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    assert len(result) == 50
    # Only one call — offset=100 would exceed total, but actually after the
    # first page items < page_size so we stop on `not page_kept_any` or
    # offset>=total. Either way, no second call.
    assert checker.vk.wall.getComments.call_count == 1


def test_safety_cap_warns_but_returns_what_we_have():
    """50 pages × 100 = 5000 — if VK keeps returning more, we stop and warn."""
    checker = _make_checker()

    # Always return a full page of in-window comments → would loop forever
    def gen_page(call_idx):
        return _page(
            [_comment(100 * call_idx + i, LATER) for i in range(100)],
            total=10000,
        )

    checker.vk.wall.getComments.side_effect = [gen_page(i) for i in range(60)]

    result = checker.check_post_comments_since(OWNER, POST, CUTOFF)

    # Hit the 50-page safety cap → 5000 comments
    assert len(result) == 5000
    assert checker.vk.wall.getComments.call_count == 50
