"""
VK API Client for SETKA project
Handles all interactions with VK API
"""

import asyncio
import logging
import threading
from typing import Any, ClassVar, Dict, List, Optional

import vk_api

from modules.vk_monitor.rate_limiter import RateLimiter, build_rate_limiter

logger = logging.getLogger(__name__)

# VK API error codes that indicate expected, recoverable conditions
# (closed wall, banned/deleted user, private community).
# https://dev.vk.com/reference/errors
_VK_EXPECTED_ERROR_CODES = frozenset({15, 18, 203, 212, 220})


def _log_vk_api_error(prefix: str, error: vk_api.exceptions.ApiError) -> None:
    """Log VK ApiError at WARNING for expected/operational codes, ERROR otherwise."""
    level = logging.WARNING if error.code in _VK_EXPECTED_ERROR_CODES else logging.ERROR
    logger.log(level, "%s: %s", prefix, error)


class VKClient:
    """VK API Client with token rotation and rate limiting.

    **Global per-token rate limit (added 2026-05-22, refactored 2026-05-26).**

    `vk_api` library уже sleep'ит при rate-limit (HTTP 6 / TooManyRequests),
    но это per-VkApi-session. Если в одном процессе живут несколько `VKClient`
    с одним и тем же токеном (например при одновременном parse'е нескольких
    регионов через разные task'и), они НЕ видят счётчик друг друга и могут
    разом отправить burst > 3 req/sec — VK ставит cooldown / captcha на токен.

    Решение: общий :class:`RateLimiter` (см. ``rate_limiter.py``). Два backend'а:

    - ``threading`` (default) — per-process через ``threading.Lock``.
    - ``redis`` — cross-process через Redis Lua-script; нужен для multi-worker
      Celery (`celery -c N` с prefork).

    Backend выбирается через env ``VK_RATE_LIMIT_BACKEND`` (default
    ``threading``). RedisRateLimiter с graceful fallback на threading при
    недоступном Redis — приоритет «не повесить», а не «строгий контроль».
    """

    # ~2.5 req/sec — чуть ниже VK-документированного лимита 3 req/sec,
    # запас на jitter в сети.
    GLOBAL_PARSE_INTERVAL_SECONDS: ClassVar[float] = 0.4

    # Lazy-singleton rate-limiter, общий для всех инстансов VKClient.
    # Сбрасывается в тестах через fixture (set to None → пересоздаётся).
    _rate_limiter: ClassVar[Optional[RateLimiter]] = None
    _rate_limiter_lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self, token: str):
        """Initialize VK client with token"""
        self.token = token
        self.session = None
        self.vk = None
        self._init_session()

    @classmethod
    def _get_rate_limiter(cls) -> RateLimiter:
        if cls._rate_limiter is None:
            with cls._rate_limiter_lock:
                if cls._rate_limiter is None:  # double-checked
                    cls._rate_limiter = build_rate_limiter(cls.GLOBAL_PARSE_INTERVAL_SECONDS)
        return cls._rate_limiter

    def _enforce_rate_limit(self) -> None:
        """Block (sleep) until `GLOBAL_PARSE_INTERVAL_SECONDS` since the last
        VK API call under the same token. Делегирует в shared RateLimiter.

        Called from `api_call` and from sync wall/groups/getById wrappers.
        Async wrappers (`get_posts`, `get_groups`, `get_messages`) — тоже
        прокидываются через `asyncio.to_thread(self._enforce_rate_limit)`,
        чтобы не блокировать event loop.
        """
        self._get_rate_limiter().wait(self.token)

    def _init_session(self):
        """Initialize VK session"""
        try:
            self.session = vk_api.VkApi(token=self.token)
            self.vk = self.session.get_api()
            logger.info("VK session initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize VK session: {e}")
            raise

    def get_wall_posts(
        self, owner_id: int, count: int = 10, offset: int = 0
    ) -> List[Dict[str, Any]]:
        """
        Get posts from VK community wall

        Args:
            owner_id: VK group ID (negative for communities)
            count: Number of posts to fetch (max 100)
            offset: Offset for pagination

        Returns:
            List of posts
        """
        try:
            self._enforce_rate_limit()
            response = self.vk.wall.get(owner_id=owner_id, count=min(count, 100), offset=offset)

            posts = response.get("items", [])
            logger.info(f"Fetched {len(posts)} posts from {owner_id}")
            return posts

        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK API error for {owner_id}", e)
            return []
        except Exception as e:
            logger.error(f"Unexpected error fetching posts from {owner_id}: {e}")
            return []

    def get_posts_by_ids(self, refs: List[tuple]) -> List[Dict[str, Any]]:
        """
        Пакетная загрузка постов wall.getById (до 100 за запрос).

        Args:
            refs: список (owner_id, post_id)

        Returns:
            Список постов в порядке ответа API (может быть короче refs при ошибках)
        """
        if not refs:
            return []
        out: List[Dict[str, Any]] = []
        batch_size = 100
        for i in range(0, len(refs), batch_size):
            chunk = refs[i : i + batch_size]
            posts_str = ",".join(f"{oid}_{pid}" for oid, pid in chunk)
            try:
                self._enforce_rate_limit()
                resp = self.vk.wall.getById(posts=posts_str)
                if resp:
                    out.extend(resp)
            except vk_api.exceptions.ApiError as e:
                _log_vk_api_error("VK wall.getById batch error", e)
            except Exception as e:
                logger.error(f"Unexpected getById batch error: {e}")
        return out

    def get_post_by_id(self, owner_id: int, post_id: int) -> Optional[Dict[str, Any]]:
        """
        Get specific post by ID

        Args:
            owner_id: VK group ID
            post_id: Post ID

        Returns:
            Post data or None
        """
        try:
            self._enforce_rate_limit()
            posts_str = f"{owner_id}_{post_id}"
            response = self.vk.wall.getById(posts=[posts_str])

            if response:
                return response[0]
            return None

        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK API error getting post {owner_id}_{post_id}", e)
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting post {owner_id}_{post_id}: {e}")
            return None

    def get_group_info(self, group_id: int) -> Optional[Dict[str, Any]]:
        """
        Get information about VK group

        Args:
            group_id: VK group ID (positive or negative)

        Returns:
            Group info or None
        """
        try:
            # Convert to positive ID if needed
            group_id = abs(group_id)

            self._enforce_rate_limit()
            response = self.vk.groups.getById(group_id=group_id)

            if response:
                return response[0]
            return None

        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK API error getting group info {group_id}", e)
            return None
        except Exception as e:
            logger.error(f"Unexpected error getting group info {group_id}: {e}")
            return None

    def search_groups(
        self,
        query: str,
        city_id: Optional[int] = None,
        count: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """Search VK groups via ``groups.search``.

        Используется модулем discovery для авто-регистрации сообществ.
        ``city_id`` — численный VK city_id (через ``database.getCities``);
        если задан, поиск ограничивается этим городом. ``count`` ≤ 1000.

        Returns:
            Список dict-ов ``{id, name, screen_name, members_count, photo_200, ...}``
            из ``response.items``. На ошибке VK API — пустой список.
        """
        if not (query or "").strip():
            return []
        try:
            self._enforce_rate_limit()
            kwargs: Dict[str, Any] = {
                "q": query,
                "count": min(int(count), 1000),
                "offset": int(offset),
                "country_id": 1,  # Russia
            }
            if city_id:
                kwargs["city_id"] = int(city_id)
            response = self.vk.groups.search(**kwargs)
            items = (response or {}).get("items", [])
            logger.info(
                "groups.search(q=%r, city_id=%s) → %d items",
                query,
                city_id,
                len(items),
            )
            return items
        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK groups.search error (q={query!r})", e)
            return []
        except Exception as e:
            logger.error(f"Unexpected groups.search error (q={query!r}): {e}")
            return []

    def get_groups_by_ids(
        self,
        group_ids: List[int],
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Batch ``groups.getById`` — до 500 id за вызов.

        ``group_ids`` — список положительных id (для отрицательных делается abs).
        ``fields`` — comma-separated extra fields VK API (например,
        ``"description,members_count,activity,status"``).

        Returns:
            Объединённый список ``items`` со всех страниц. Ошибки одного chunk'а
            не валят весь вызов (chunk пропускается, остальные продолжаются).
        """
        if not group_ids:
            return []
        out: List[Dict[str, Any]] = []
        batch_size = 500
        ids = [abs(int(g)) for g in group_ids]
        for i in range(0, len(ids), batch_size):
            chunk = ids[i : i + batch_size]
            try:
                self._enforce_rate_limit()
                kwargs: Dict[str, Any] = {"group_ids": chunk}
                if fields:
                    kwargs["fields"] = fields
                resp = self.vk.groups.getById(**kwargs)
                if resp:
                    out.extend(resp)
            except vk_api.exceptions.ApiError as e:
                _log_vk_api_error(f"VK groups.getById batch error (size={len(chunk)})", e)
            except Exception as e:
                logger.error(f"Unexpected groups.getById batch error (size={len(chunk)}): {e}")
        return out

    def get_groups_by_refs(
        self,
        refs: List[str],
        fields: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Batch ``groups.getById`` по строковым refs (screen_name / ``club123``).

        В отличие от :meth:`get_groups_by_ids` (только числовые id, делает abs),
        принимает текстовые refs — например, screen_name'ы из блока «Ссылки»
        главной ИНФО-страницы района. VK молча отбрасывает пользовательские
        домены (getById вернёт только сообщества), так что лишние refs безопасны.

        Args:
            refs: список screen_name / ``club<id>`` строк (до 500 за вызов).
            fields: comma-separated extra fields VK API.

        Returns:
            Объединённый список ``items``. Ошибка chunk'а не валит весь вызов.
        """
        clean = [str(r).strip() for r in (refs or []) if str(r or "").strip()]
        if not clean:
            return []
        out: List[Dict[str, Any]] = []
        batch_size = 500
        for i in range(0, len(clean), batch_size):
            chunk = clean[i : i + batch_size]
            try:
                self._enforce_rate_limit()
                kwargs: Dict[str, Any] = {"group_ids": ",".join(chunk)}
                if fields:
                    kwargs["fields"] = fields
                resp = self.vk.groups.getById(**kwargs)
                if resp:
                    out.extend(resp)
            except vk_api.exceptions.ApiError as e:
                _log_vk_api_error(f"VK groups.getById refs error (size={len(chunk)})", e)
            except Exception as e:
                logger.error(f"Unexpected groups.getById refs error (size={len(chunk)}): {e}")
        return out

    def resolve_city(
        self,
        query: str,
        country_id: int = 1,
        count: int = 20,
    ) -> List[Dict[str, Any]]:
        """Resolve city name → ``vk_city_id`` via ``database.getCities``.

        Используется wizard'ом нового региона: модератор вводит «Малмыж»,
        возвращается список ``[{id, title, region, area}, …]`` для dropdown.

        ``q`` для ``database.getCities`` ищет по подстроке (case-insensitive),
        VK сортирует по importance. ``count`` ≤ 100.

        Returns:
            Список dict с полями ``id`` (vk_city_id), ``title``, ``area``,
            ``region``. На ошибке — пустой список.
        """
        if not (query or "").strip():
            return []
        try:
            self._enforce_rate_limit()
            response = self.vk.database.getCities(
                country_id=int(country_id),
                q=query,
                count=min(int(count), 100),
            )
            items = (response or {}).get("items", [])
            logger.info(
                "database.getCities(q=%r) → %d cities",
                query,
                len(items),
            )
            return items
        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK database.getCities error (q={query!r})", e)
            return []
        except Exception as e:
            logger.error(f"Unexpected database.getCities error (q={query!r}): {e}")
            return []

    def parse_attachments(self, post: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        Parse post attachments (photos, videos, links, etc.)

        Args:
            post: VK post data

        Returns:
            List of parsed attachments
        """
        attachments = []

        if "attachments" not in post:
            return attachments

        for att in post["attachments"]:
            att_type = att.get("type")

            if att_type == "photo":
                photo = att["photo"]
                # Get largest photo size
                sizes = photo.get("sizes", [])
                if sizes:
                    largest = max(sizes, key=lambda x: x.get("width", 0) * x.get("height", 0))
                    attachments.append(
                        {
                            "type": "photo",
                            "url": largest.get("url"),
                            "width": largest.get("width"),
                            "height": largest.get("height"),
                        }
                    )

            elif att_type == "video":
                video = att["video"]
                attachments.append(
                    {
                        "type": "video",
                        "title": video.get("title"),
                        "duration": video.get("duration"),
                        "views": video.get("views", 0),
                    }
                )

            elif att_type == "link":
                link = att["link"]
                attachments.append(
                    {"type": "link", "url": link.get("url"), "title": link.get("title")}
                )

            elif att_type == "doc":
                doc = att["doc"]
                attachments.append(
                    {"type": "document", "title": doc.get("title"), "url": doc.get("url")}
                )

        return attachments

    def extract_post_stats(self, post: Dict[str, Any]) -> Dict[str, int]:
        """
        Extract statistics from post

        Args:
            post: VK post data

        Returns:
            Dictionary with stats
        """
        return {
            "views": post.get("views", {}).get("count", 0),
            "likes": post.get("likes", {}).get("count", 0),
            "reposts": post.get("reposts", {}).get("count", 0),
            "comments": post.get("comments", {}).get("count", 0),
        }

    async def get_user_info(self) -> Optional[Dict[str, Any]]:
        """
        Get current user information

        Returns:
            User info or None
        """
        try:
            await asyncio.to_thread(self._enforce_rate_limit)
            response = self.vk.users.get()
            if response:
                return response[0]
            return None
        except Exception as e:
            logger.error(f"Error getting user info: {e}")
            return None

    async def get_posts(
        self, owner_id: int, count: int = 10, offset: int = 0, extended: int = 0
    ) -> Optional[Dict[str, Any]]:
        """
        Get posts from VK wall (async wrapper)

        Args:
            owner_id: VK group ID (negative for communities)
            count: Number of posts to fetch (max 100)
            offset: Offset for pagination
            extended: Extended information

        Returns:
            Posts data or None
        """
        try:
            await asyncio.to_thread(self._enforce_rate_limit)
            response = self.vk.wall.get(
                owner_id=owner_id, count=min(count, 100), offset=offset, extended=extended
            )
            return response
        except Exception as e:
            logger.error(f"Error getting posts from {owner_id}: {e}")
            return None

    async def get_groups(self, count: int = 10, extended: int = 1) -> Optional[Dict[str, Any]]:
        """
        Get user's groups

        Args:
            count: Number of groups to fetch
            extended: Extended information

        Returns:
            Groups data or None
        """
        try:
            await asyncio.to_thread(self._enforce_rate_limit)
            response = self.vk.groups.get(count=count, extended=extended)
            return response
        except Exception as e:
            logger.error(f"Error getting groups: {e}")
            return None

    async def get_messages(self, count: int = 10) -> Optional[Dict[str, Any]]:
        """
        Get user's conversations (used to probe `messages` permission).

        VK API `messages.get` has been deprecated/removed since 2016;
        `messages.getConversations` is the modern replacement and only
        succeeds when the token actually carries the `messages` scope.
        """
        try:
            await asyncio.to_thread(self._enforce_rate_limit)
            response = self.vk.messages.getConversations(count=count)
            return response
        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error("Error getting messages", e)
            return None
        except Exception as e:
            logger.error(f"Error getting messages: {e}")
            return None

    async def get_message_history(
        self,
        peer_id: int,
        count: int = 50,
        group_id: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch a conversation thread via `messages.getHistory` (ad cabinet, block A).

        Used by the thread-view of an inbound-DM ad request: show the recent
        back-and-forth before the operator replies. `group_id` is required when
        the token is a user-token acting on behalf of a community (positive id);
        community access tokens imply the group and ignore it.

        Returns the raw VK response (`items` newest-last, `profiles`/`groups`
        via `extended=1`) or None on error — the caller decides how to degrade.
        """
        try:
            await asyncio.to_thread(self._enforce_rate_limit)
            params: Dict[str, Any] = {
                "peer_id": int(peer_id),
                "count": min(int(count), 200),
                "extended": 1,
            }
            if group_id:
                params["group_id"] = abs(int(group_id))
            return self.vk.messages.getHistory(**params)
        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error("Error getting message history", e)
            return None
        except Exception as e:
            logger.error(f"Error getting message history for peer {peer_id}: {e}")
            return None

    async def check_token_validity(self) -> bool:
        """
        Check if token is still valid

        Returns:
            True if token is valid, False otherwise
        """
        try:
            # Try to fetch user info
            user_info = await self.get_user_info()
            return user_info is not None
        except Exception as e:
            logger.error(f"Token validation failed: {e}")
            return False

    def api_call(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Make a synchronous VK API call (for use with VKPublisher).

        Args:
            method: VK API method name (e.g., 'wall.post', 'photos.getWallUploadServer')
            params: Method parameters

        Returns:
            VK API response dict
        """
        try:
            self._enforce_rate_limit()
            response = self.session.method(method, params)
            return response
        except vk_api.exceptions.ApiError as e:
            _log_vk_api_error(f"VK API error ({method})", e)
            # Pass through error_code so callers can implement smart retries
            # (e.g. publisher fallback to publish-token on code 15/27).
            return {
                "error": {
                    "error_code": int(getattr(e, "code", 0) or 0),
                    "error_msg": str(e),
                }
            }
        except Exception as e:
            logger.error(f"Unexpected error in {method}: {e}")
            return {"error": {"error_msg": str(e)}}


class VKTokenRotator:
    """Rotates between multiple VK tokens to avoid rate limits"""

    def __init__(self, tokens: List[str]):
        """
        Initialize token rotator

        Args:
            tokens: List of VK API tokens
        """
        self.tokens = tokens
        self.current_index = 0
        self.clients = [VKClient(token) for token in tokens if token]

    def get_client(self) -> Optional[VKClient]:
        """
        Get next available VK client

        Returns:
            VKClient instance or None if no clients available
        """
        if not self.clients:
            logger.error("No VK clients available")
            return None

        client = self.clients[self.current_index]
        self.current_index = (self.current_index + 1) % len(self.clients)

        return client

    async def check_all_tokens(self) -> int:
        """
        Check validity of all tokens

        Returns:
            Number of valid tokens
        """
        valid_count = 0

        for client in self.clients:
            if await client.check_token_validity():
                valid_count += 1

        return valid_count
