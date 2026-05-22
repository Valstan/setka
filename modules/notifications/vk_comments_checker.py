"""
VK Recent Comments Checker

Собирает комментарии под постами сообществ SETKA за последние 24 часа.

Подход:
- Берём список постов (owner_id + post_id) из БД (обычно posts за сутки).
- Для каждого поста вызываем VK API wall.getComments (последние N комментариев).
- Фильтруем по времени комментария (date >= cutoff_ts).

Это намного эффективнее, чем обходить все сообщества.
"""

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from vk_api.exceptions import ApiError

from modules.notifications.base_checker import BaseVKChecker

logger = logging.getLogger(__name__)


class VKCommentsChecker(BaseVKChecker):
    """Проверка комментариев под постами VK с пагинацией и thread.items.

    Token routing + community-token fallback handled by BaseVKChecker.
    """

    CHECKER_NAME = "VK Comments Checker"

    # Per-post safety cap to bound runtime on viral threads. 50 pages × 100 = 5000
    # comments per single post is enough for any realistic news discussion;
    # raise here if needed.
    _PAGES_PER_POST_LIMIT = 50
    _PAGE_SIZE = 100

    def check_post_comments_since(
        self,
        owner_id: int,
        post_id: int,
        cutoff_ts: int,
        count: int = 100,  # kept for back-compat; not used (we paginate by _PAGE_SIZE)
    ) -> List[Dict[str, Any]]:
        """Получить ВСЕ комментарии под постом за период [cutoff_ts, now].

        Пагинируется по `offset` (VK возвращает не больше 100 за раз) до тех
        пор пока:
          - вернулся пустой items (стена закрыта / закончились комменты), ИЛИ
          - все комменты последней партии старше cutoff_ts (мы прошли границу
            окна 24h), ИЛИ
          - достигли safety cap `_PAGES_PER_POST_LIMIT` (vк-viral-thread защита).

        Каждый коммент дополнительно распаковывает свой `thread.items` (ответы
        первого уровня) — в плоский список с `parent_id` и `is_reply=True`. VK
        отдаёт thread inline в ответе wall.getComments при `thread_items=1`.

        При неуспехе через community-token (code 15/27) автоматически
        повторяет через user-token.
        """
        recent: List[Dict[str, Any]] = []
        offset = 0
        pages = 0

        while pages < self._PAGES_PER_POST_LIMIT:

            def call(api, _offset=offset):
                return api.wall.getComments(
                    owner_id=owner_id,
                    post_id=post_id,
                    need_likes=0,
                    extended=1,  # join profiles[]/groups[] (для имени автора)
                    thread_items=1,  # вернуть thread.items (ответы на коммент)
                    count=self._PAGE_SIZE,
                    offset=_offset,
                    sort="desc",  # новые сверху → можно прерваться при выходе за cutoff
                )

            try:
                resp, _via = self._call_with_fallback(owner_id, "wall.getComments", call)
            except ApiError as e:
                logger.debug(
                    f"VK API error while fetching comments for wall{owner_id}_{post_id} "
                    f"(offset {offset}): {e} (code: {e.code})"
                )
                break
            except Exception as e:
                logger.warning(
                    f"Error while fetching comments for wall{owner_id}_{post_id} "
                    f"(offset {offset}): {e}"
                )
                break

            items = resp.get("items", []) or []
            if not items:
                break

            page_kept_any = False
            page_oldest_after_cutoff = False  # True если хотя бы один коммент страницы новее cutoff

            for c in items:
                c_date = c.get("date")
                if not c_date:
                    continue
                if int(c_date) >= cutoff_ts:
                    page_oldest_after_cutoff = True
                    recent.append(c)
                    page_kept_any = True
                    # Распаковка thread.items — ответы первого уровня.
                    thread = c.get("thread") or {}
                    thread_items = thread.get("items") or []
                    for t in thread_items:
                        t_date = t.get("date")
                        if t_date and int(t_date) >= cutoff_ts:
                            t["parent_id"] = c.get("id")
                            t["is_reply"] = True
                            recent.append(t)

            pages += 1
            offset += self._PAGE_SIZE

            # Прерываем когда ВСЯ страница уже старше cutoff_ts (sort=desc).
            # Если page_kept_any=False — значит самый «новый» из этой страницы
            # уже за пределами окна, следующая будет ещё старше.
            if not page_kept_any:
                break

            total_in_post = resp.get("count") or 0
            if offset >= int(total_in_post):
                break

        if pages == self._PAGES_PER_POST_LIMIT:
            logger.warning(
                f"wall{owner_id}_{post_id}: reached safety cap "
                f"({self._PAGES_PER_POST_LIMIT} pages × {self._PAGE_SIZE}); "
                f"some older comments may be missing"
            )

        return recent

    def _get_recent_wall_posts_with_comments(
        self,
        owner_id: int,
        cutoff_ts: int,
        count: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Получить посты со стены owner_id (обычно группа) за последние 24 часа, у которых есть комментарии.

        Был баг: `self.vk` использовался напрямую вместо `_api_for(owner_id)`,
        из-за чего community-токены сюда не доходили (см. PR 2026-05-21).
        Теперь маршрут одинаковый с `check_post_comments_since` + fallback.
        """

        def call(api):
            return api.wall.get(owner_id=owner_id, count=count)

        try:
            resp, _via = self._call_with_fallback(owner_id, "wall.get", call)
        except ApiError as e:
            logger.debug(
                f"VK API error while fetching wall for owner_id={owner_id}: {e} (code: {e.code})"
            )
            return []
        except Exception as e:
            logger.warning(f"Error while fetching wall for owner_id={owner_id}: {e}")
            return []

        items = resp.get("items", []) or []
        recent_posts = []
        for p in items:
            p_date = p.get("date")
            if not p_date:
                continue
            if int(p_date) < cutoff_ts:
                continue
            comments_meta = p.get("comments") or {}
            comments_count = comments_meta.get("count", 0) or 0
            if comments_count <= 0:
                continue
            recent_posts.append(p)
        return recent_posts

    async def check_recent_comments_for_posts(
        self,
        posts: List[Dict[str, Any]],
        cutoff_ts: int,
        max_posts: int = 200,
        max_comments_per_post: int = 100,  # back-compat; not used (per-post pagination)
        max_total_comments: int = 5000,
    ) -> List[Dict[str, Any]]:
        """Проверить комментарии для списка постов с пагинацией и threads.

        В отличие от старой логики, **не обрывает** обход на первых 300
        комментариях: лимит `max_total_comments` теперь служит safety-капом
        от рассасывания памяти (5000 ≈ 5 МБ JSON), а не «нашли N — стоп
        и пропускаем остальные посты». Все посты сканируются всегда.
        """
        notifications: List[Dict[str, Any]] = []
        now_iso = datetime.utcnow().isoformat()

        for p in posts[:max_posts]:
            owner_id = int(p["vk_owner_id"])
            post_id = int(p["vk_post_id"])

            comments = self.check_post_comments_since(
                owner_id=owner_id,
                post_id=post_id,
                cutoff_ts=cutoff_ts,
            )
            if not comments:
                continue

            post_url = f"https://vk.com/wall{owner_id}_{post_id}"
            for c in comments:
                if len(notifications) >= max_total_comments:
                    logger.warning(
                        f"Reached max_total_comments safety cap "
                        f"({max_total_comments}); truncating output"
                    )
                    return self._sort_newest_first(notifications)
                notif = self._build_comment_notification(
                    c,
                    post=p,
                    owner_id=owner_id,
                    post_id=post_id,
                    post_url=post_url,
                    now_iso=now_iso,
                )
                if notif is not None:
                    notifications.append(notif)

        logger.info(f"Found {len(notifications)} recent comments notifications")
        return self._sort_newest_first(notifications)

    @staticmethod
    def _build_comment_notification(
        c: Dict[str, Any],
        *,
        post: Dict[str, Any],
        owner_id: int,
        post_id: int,
        post_url: str,
        now_iso: str,
    ) -> Optional[Dict[str, Any]]:
        """Map a raw VK comment dict to a notification entry. Returns None for
        empty-text comments (sticker-only, attachments-only) — but they still
        bump the per-post counter via the caller, just aren't surfaced."""
        text = (c.get("text") or "").strip()
        if not text:
            return None
        c_date = c.get("date")
        return {
            "type": "recent_comment",
            "region_id": post.get("region_id"),
            "region_name": post.get("region_name"),
            "region_code": post.get("region_code"),
            "community_id": post.get("community_id"),
            "community_name": post.get("community_name"),
            "vk_owner_id": owner_id,
            "vk_post_id": post_id,
            "comment_id": c.get("id"),
            "parent_id": c.get("parent_id"),
            "is_reply": bool(c.get("is_reply")),
            "from_id": c.get("from_id"),
            "likes_count": (c.get("likes") or {}).get("count", 0),
            "has_attachments": bool(c.get("attachments")),
            "text": text,
            "post_url": post_url,
            "commented_at": datetime.utcfromtimestamp(int(c_date)).isoformat() if c_date else None,
            "checked_at": now_iso,
        }

    @staticmethod
    def _sort_newest_first(notifications: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        notifications.sort(key=lambda n: n.get("commented_at") or "", reverse=True)
        return notifications

    async def check_recent_comments_for_region_groups(
        self,
        region_groups: List[Dict[str, Any]],
        cutoff_ts: int,
        max_posts_per_group: int = 50,
        max_comments_per_post: int = 100,  # back-compat; per-post pagination
        max_total_comments: int = 5000,
    ) -> List[Dict[str, Any]]:
        """
        Собрать комментарии за последние 24 часа из главных региональных групп (ИНФО).

        Старая логика обрывала обход на 300 комментариях, теряя посты-хвосты.
        Сейчас `max_total_comments` — только safety-кап от raw-памяти.
        Каждый пост сканируется полностью через `check_post_comments_since`,
        включая ответы первого уровня (`thread.items`).
        """
        notifications: List[Dict[str, Any]] = []
        now_iso = datetime.utcnow().isoformat()

        for g in region_groups:
            group_id = g.get("vk_group_id")
            if not group_id:
                continue

            owner_id = int(group_id)
            recent_posts = self._get_recent_wall_posts_with_comments(
                owner_id=owner_id,
                cutoff_ts=cutoff_ts,
                count=max_posts_per_group,
            )

            for p in recent_posts:
                post_id = p.get("id")
                if not post_id:
                    continue

                comments = self.check_post_comments_since(
                    owner_id=owner_id,
                    post_id=int(post_id),
                    cutoff_ts=cutoff_ts,
                )
                if not comments:
                    continue

                post_url = f"https://vk.com/wall{owner_id}_{int(post_id)}"
                # Главная ИНФО-группа: post-context берём из региона, не из p
                # (т.к. p — это сырой dict от VK API, а не db record).
                post_context = {
                    "region_id": g.get("region_id"),
                    "region_name": g.get("region_name"),
                    "region_code": g.get("region_code"),
                    "community_id": None,
                    "community_name": g.get("region_name"),
                }
                for c in comments:
                    if len(notifications) >= max_total_comments:
                        logger.warning(
                            f"Reached max_total_comments safety cap "
                            f"({max_total_comments}); truncating output"
                        )
                        return self._sort_newest_first(notifications)
                    notif = self._build_comment_notification(
                        c,
                        post=post_context,
                        owner_id=owner_id,
                        post_id=int(post_id),
                        post_url=post_url,
                        now_iso=now_iso,
                    )
                    if notif is not None:
                        notifications.append(notif)

        logger.info(
            f"Found {len(notifications)} recent comments notifications "
            f"(main INFO groups only, threads included)"
        )
        return self._sort_newest_first(notifications)
