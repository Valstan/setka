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
from typing import List, Dict, Any, Optional
from datetime import datetime

import vk_api
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


class VKCommentsChecker:
    """Проверка комментариев под постами VK.

    Поддерживает community access tokens: если у группы (owner_id) есть свой
    токен в `vk_tokens.community_id`, читать комментарии будем под ним.
    Иначе fallback на пользовательский токен. Снимает нагрузку с VALSTAN/VITA.
    """

    def __init__(self, vk_token: str, community_tokens: Optional[Dict[int, str]] = None):
        try:
            self.session = vk_api.VkApi(token=vk_token)
            self.vk = self.session.get_api()
            self.community_tokens = dict(community_tokens or {})
            logger.info(
                "VK Comments Checker initialized (community tokens: %d)",
                len(self.community_tokens),
            )
        except Exception as e:
            logger.error(f"Failed to initialize VK Comments Checker: {e}")
            raise

    # VK API error codes where community-token typically fails and we should
    # retry via user-token (no manage/wall scope, group auth restrictions).
    _COMMUNITY_FALLBACK_CODES = {15, 27}

    def _api_for(self, group_id: int):
        cid = abs(int(group_id))
        tok = self.community_tokens.get(cid)
        if tok:
            return vk_api.VkApi(token=tok).get_api(), True
        return self.vk, False

    def _call_with_fallback(self, group_id: int, op_name: str, fn):
        """Run VK API call via community-token if available; on code 15/27 retry via user-token.

        `fn(api)` must perform the actual VK API call given a vk_api handle.
        Returns (response, via_label) or raises the final exception.
        """
        api, via_community = self._api_for(group_id)
        try:
            return fn(api), ("community-token" if via_community else "user-token")
        except ApiError as e:
            if via_community and e.code in self._COMMUNITY_FALLBACK_CODES:
                logger.info(
                    "Group %s: community-token failed on %s with code %s, retrying via user-token",
                    group_id, op_name, e.code,
                )
                return fn(self.vk), "community-fallback-user"
            raise

    def check_post_comments_since(
        self,
        owner_id: int,
        post_id: int,
        cutoff_ts: int,
        count: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Получить комментарии под постом начиная с cutoff_ts (unix seconds).

        При неуспехе через community-token (code 15/27) автоматически повторяет
        через user-token.
        """
        def call(api):
            return api.wall.getComments(
                owner_id=owner_id,
                post_id=post_id,
                need_likes=0,
                extended=0,
                count=count,
                sort="desc",
            )

        try:
            resp, _via = self._call_with_fallback(owner_id, "wall.getComments", call)
        except ApiError as e:
            logger.debug(
                f"VK API error while fetching comments for wall{owner_id}_{post_id}: {e} (code: {e.code})"
            )
            return []
        except Exception as e:
            logger.warning(f"Error while fetching comments for wall{owner_id}_{post_id}: {e}")
            return []

        items = resp.get("items", []) or []
        recent = []
        for c in items:
            c_date = c.get("date")
            if not c_date:
                continue
            if int(c_date) >= cutoff_ts:
                recent.append(c)
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
            logger.debug(f"VK API error while fetching wall for owner_id={owner_id}: {e} (code: {e.code})")
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
        max_comments_per_post: int = 100,
        max_total_comments: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Проверить комментарии для списка постов.

        Args:
            posts: Список dict с полями:
                - region_id, region_name, region_code
                - community_id, community_name
                - vk_owner_id, vk_post_id
            cutoff_ts: unix seconds (граница "за сутки")

        Returns:
            Список уведомлений (по одному на комментарий).
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
                count=max_comments_per_post,
            )

            if not comments:
                continue

            post_url = f"https://vk.com/wall{owner_id}_{post_id}"

            for c in comments:
                comment_id = c.get("id")
                text = (c.get("text") or "").strip()
                c_date = c.get("date")
                if not text:
                    # Пустые комментарии (стикеры/вложения) пока пропускаем
                    continue

                notifications.append(
                    {
                        "type": "recent_comment",
                        "region_id": p.get("region_id"),
                        "region_name": p.get("region_name"),
                        "region_code": p.get("region_code"),
                        "community_id": p.get("community_id"),
                        "community_name": p.get("community_name"),
                        "vk_owner_id": owner_id,
                        "vk_post_id": post_id,
                        "comment_id": comment_id,
                        "text": text,
                        "post_url": post_url,
                        "commented_at": datetime.utcfromtimestamp(int(c_date)).isoformat()
                        if c_date
                        else None,
                        "checked_at": now_iso,
                    }
                )

                if len(notifications) >= max_total_comments:
                    logger.info(f"Reached max_total_comments={max_total_comments}, stopping early")
                    return notifications

        # Новые сверху
        notifications.sort(key=lambda n: n.get("commented_at") or "", reverse=True)
        logger.info(f"Found {len(notifications)} recent comments notifications")
        return notifications

    async def check_recent_comments_for_region_groups(
        self,
        region_groups: List[Dict[str, Any]],
        cutoff_ts: int,
        max_posts_per_group: int = 50,
        max_comments_per_post: int = 100,
        max_total_comments: int = 300,
    ) -> List[Dict[str, Any]]:
        """
        Собрать комментарии за последние 24 часа только из главных региональных групп (ИНФО).

        Args:
            region_groups: Список dict с полями:
                - region_id, region_name, region_code, vk_group_id
            cutoff_ts: unix seconds
        """
        notifications: List[Dict[str, Any]] = []
        now_iso = datetime.utcnow().isoformat()

        for g in region_groups:
            group_id = g.get("vk_group_id")
            if not group_id:
                continue

            owner_id = int(group_id)  # для групп в базе уже отрицательный
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
                    count=max_comments_per_post,
                )
                if not comments:
                    continue

                post_url = f"https://vk.com/wall{owner_id}_{int(post_id)}"

                for c in comments:
                    comment_id = c.get("id")
                    text = (c.get("text") or "").strip()
                    c_date = c.get("date")
                    if not text:
                        continue

                    notifications.append(
                        {
                            "type": "recent_comment",
                            "region_id": g.get("region_id"),
                            "region_name": g.get("region_name"),
                            "region_code": g.get("region_code"),
                            "community_id": None,
                            "community_name": g.get("region_name"),  # главная ИНФО-группа
                            "vk_owner_id": owner_id,
                            "vk_post_id": int(post_id),
                            "comment_id": comment_id,
                            "text": text,
                            "post_url": post_url,
                            "commented_at": datetime.utcfromtimestamp(int(c_date)).isoformat()
                            if c_date
                            else None,
                            "checked_at": now_iso,
                        }
                    )

                    if len(notifications) >= max_total_comments:
                        logger.info(f"Reached max_total_comments={max_total_comments}, stopping early")
                        return notifications

        # Новые сверху
        notifications.sort(key=lambda n: n.get("commented_at") or "", reverse=True)
        logger.info(f"Found {len(notifications)} recent comments notifications (main INFO groups only)")
        return notifications


