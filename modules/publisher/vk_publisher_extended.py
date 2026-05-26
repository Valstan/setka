"""
VK Publisher - Publishes digest posts to VK groups

Migrated from old_postopus bin/rw/post_msg.py and bin/rw/posting_post.py publishing logic.
Handles VK API wall.post with proper error handling and token rotation.
"""

import asyncio
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple

# Imported lazily inside __init__ to avoid hard deps for test fixtures.

if TYPE_CHECKING:
    from modules.aggregation.aggregator import AggregatedPost

logger = logging.getLogger(__name__)


class VKPublisher:
    """
    Publishes posts to VK groups.

    IMPORTANT: This class creates its OWN VK client with the PUBLISH token.
    Never reuse a parsing client for publishing.

    Community access tokens (опционально): передаются как
    `community_tokens={abs(group_id): token}`. Если для целевой группы есть
    community-токен — публикуем под ним. Это и снимает нагрузку с VALSTAN/VITA,
    и работает корректно даже если у user-токена нет нужных прав на стену
    этой группы.
    """

    # VK API limits
    POSTS_PER_DAY_LIMIT = 50  # Per group
    POST_INTERVAL_SECONDS = 5  # Minimum interval between posts to the SAME group

    # Global rate-limit on the shared publish-token (VALSTAN). Without this,
    # a burst of 13 wall.repost calls in 7 sec earns us a [0] Captcha needed
    # response from VK (observed on 2026-05-21 14:37 right after the
    # community→publish fallback started working). Empirically ~1.5s between
    # publish-token API calls stays under VK's anti-burst threshold without
    # noticeably slowing the hourly copy-setka cycle (13 calls × 1.5 ≈ 20s).
    GLOBAL_PUBLISH_INTERVAL_SECONDS = 1.5

    # Class-level state: one publish-token per process, shared across all
    # VKPublisher instances. Hence a class-var (not instance attribute).
    _last_publish_token_call: ClassVar[Optional[datetime]] = None
    _publish_token_lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # VK error codes where a community-token wall.* call should be retried
    # via the global publish-token. Community access tokens issued via
    # vk.com/club{ID}→API typically lack `manage` scope.
    _COMMUNITY_FALLBACK_CODES = {15, 27}

    # VK API methods that are KNOWN to be unsupported with group access tokens
    # — VK docs explicitly state these need a user token. We don't even try
    # the community-token first to save a guaranteed-failure round trip and
    # to keep the publish-token rate-limit budget intact.
    _USER_TOKEN_ONLY_METHODS = frozenset({"wall.repost"})

    def __init__(
        self,
        vk_client=None,
        test_polygon_mode: bool = False,
        test_polygon_group_id: int = -137760500,
        community_tokens: Optional[Dict[int, str]] = None,
    ):
        """
        Args:
            vk_client: Optional VK API client. If None, creates one with the publish token.
            test_polygon_mode: If True, post to test group instead
            test_polygon_group_id: Test polygon VK group ID
            community_tokens: {abs(group_id): token} per-community access tokens —
                имеют приоритет над общим publish-токеном для своих групп.
        """
        from config.runtime import get_publish_token
        from modules.vk_monitor.vk_client import VKClient

        if vk_client is not None:
            # Use provided client (for tests that already set up correctly)
            self.vk_client = vk_client
        else:
            # Create own client with PUBLISH token
            publish_token = get_publish_token()
            if not publish_token:
                raise RuntimeError(
                    "No VK publish token configured. Set VK_PUBLISH_TOKEN_NAME=VALSTAN in env."
                )
            self.vk_client = VKClient(publish_token)
            logger.info("VKPublisher: created own client with publish token")

        self.test_polygon_mode = test_polygon_mode
        self.test_polygon_group_id = test_polygon_group_id
        self._last_post_time = {}  # group_id -> datetime
        self._community_tokens = dict(community_tokens or {})
        # Кеш community-клиентов (VKClient инициализирует session, не хочется делать заново).
        self._community_clients: Dict[int, Any] = {}
        if self._community_tokens:
            logger.info(
                "VKPublisher: %d community tokens available for per-group publish",
                len(self._community_tokens),
            )

    def _client_for_group(self, target_group_id: int):
        """Возвращает клиент, под которым нужно постить в эту группу.

        Если есть community-токен — используется он (создаётся VKClient ленивно).
        Иначе — общий publish-клиент (`self.vk_client`).
        """
        cid = abs(int(target_group_id))
        tok = self._community_tokens.get(cid)
        if not tok:
            return self.vk_client, False
        cli = self._community_clients.get(cid)
        if cli is None:
            from modules.vk_monitor.vk_client import VKClient

            cli = VKClient(tok)
            self._community_clients[cid] = cli
        return cli, True

    async def publish_digest(
        self,
        group_id: int,
        text: str,
        attachments: List[str] = None,
        copyright_url: str = None,
        from_group: bool = True,
    ) -> Dict[str, Any]:
        """
        Publish digest post to VK group.

        Args:
            group_id: VK group ID (negative number)
            text: Post text
            attachments: List of VK attachment strings
            copyright_url: Copyright URL for attribution
            from_group: True = post as group, False = as user

        Returns:
            VK API response dict with post_id, url, etc.
        """
        normalized_group_id = self._normalize_group_owner_id(group_id)

        # Determine target group
        if self.test_polygon_mode:
            target_group_id = self._normalize_group_owner_id(self.test_polygon_group_id)
            logger.info(f"🧪 TEST POLYGON MODE: Posting to test group {target_group_id}")
        else:
            target_group_id = normalized_group_id

        # Rate limiting
        await self._enforce_rate_limit(target_group_id)

        # Prepare attachments
        attachments_str = ",".join(attachments) if attachments else ""

        # Build wall.post parameters
        params = {
            "owner_id": target_group_id,
            "message": text,
            "from_group": 1 if from_group else 0,
        }

        if attachments_str:
            params["attachments"] = attachments_str

        if copyright_url:
            params["copyright"] = copyright_url

        # Execute wall.post под правильным клиентом (community-токен, если есть).
        client, _via_community = self._client_for_group(target_group_id)
        try:
            response, via = await self._call_wall_post(params, client=client)

            post_id = response.get("post_id")
            post_url = f"https://vk.com/wall{target_group_id}_{post_id}"

            logger.info(
                "✅ Published post %s to group %s (via %s)",
                post_id,
                target_group_id,
                via,
            )
            logger.info(f"   URL: {post_url}")

            # Track last post time
            self._last_post_time[target_group_id] = datetime.now()

            return {
                "success": True,
                "post_id": post_id,
                "owner_id": target_group_id,
                "url": post_url,
                "text_length": len(text),
                "attachments_count": len(attachments) if attachments else 0,
            }

        except Exception as e:
            logger.error(f"❌ Failed to publish post to group {target_group_id}: {e}")

            return {
                "success": False,
                "error": str(e),
                "group_id": target_group_id,
            }

    async def publish_repost(
        self,
        group_id: int,
        source_owner_id: int,
        source_post_id: int,
        message: str = "",
    ) -> Dict[str, Any]:
        """
        Repost from another group.

        Uses VK API wall.repost

        Args:
            group_id: Target group ID
            source_owner_id: Source post owner ID
            source_post_id: Source post ID
            message: Optional message to add

        Returns:
            VK API response dict
        """
        normalized_group_id = self._normalize_group_owner_id(group_id)

        if self.test_polygon_mode:
            target_group_id = self._normalize_group_owner_id(self.test_polygon_group_id)
        else:
            target_group_id = normalized_group_id

        # Rate limiting
        await self._enforce_rate_limit(target_group_id)

        # Build repost object string
        repost_object = f"wall{source_owner_id}_{source_post_id}"

        # VK API expects `object` (e.g. wall-123_456), not an informal `repost` key.
        params = {
            "object": repost_object,
            "message": message,
        }
        if target_group_id < 0:
            params["group_id"] = abs(target_group_id)

        # wall.repost is user-token-only per VK API; _call_wall_post recognises
        # this via _USER_TOKEN_ONLY_METHODS and routes through publish-token.
        # We still pass a client (community) just to keep the signature uniform,
        # but it'll be ignored by _call_wall_post for repost.
        client, _via_community = self._client_for_group(target_group_id)
        try:
            response, via = await self._call_wall_post(
                params,
                method="wall.repost",
                client=client,
            )

            post_id = response.get("post_id")
            success = response.get("success", 0) == 1

            if success:
                post_url = f"https://vk.com/wall{target_group_id}_{post_id}"
                logger.info(
                    "✅ Reposted %s_%s to %s (via %s)",
                    source_owner_id,
                    source_post_id,
                    target_group_id,
                    via,
                )

                self._last_post_time[target_group_id] = datetime.now()

                return {
                    "success": True,
                    "post_id": post_id,
                    "owner_id": target_group_id,
                    "url": post_url,
                    "reposted": True,
                }
            else:
                return {
                    "success": False,
                    "error": "VK API returned success=0",
                }

        except Exception as e:
            logger.error(f"❌ Failed to repost to group {target_group_id}: {e}")

            return {
                "success": False,
                "error": str(e),
            }

    async def _call_wall_post(
        self,
        params: Dict[str, Any],
        method: str = "wall.post",
        client=None,
    ) -> Tuple[Dict, str]:
        """
        Call VK API wall.post / wall.repost.

        Strategy:
        1. If method is in `_USER_TOKEN_ONLY_METHODS` (e.g. wall.repost which
           VK fundamentally doesn't allow with a group token), bypass any
           community-client and go straight to publish-token.
        2. Otherwise use `client` if provided (community-token, when available
           for the target group).
        3. On VK error 15 or 27 from a community-client, retry via publish-token.
        4. All publish-token calls go through `_enforce_publish_token_rate_limit`
           which globally throttles VALSTAN to one API call every
           GLOBAL_PUBLISH_INTERVAL_SECONDS — fights VK's anti-burst captcha.

        Returns:
            (response, via_label) where via_label is one of
            'publish-token' / 'community-token' / 'community-fallback-publish'.
        """
        # Step 1: VK API restrictions — never try a group-token call we know fails
        if method in self._USER_TOKEN_ONLY_METHODS:
            await self._enforce_publish_token_rate_limit()
            response = await self._invoke(self.vk_client, method, params)
            return response, "publish-token"

        primary_client = client if client is not None else self.vk_client
        is_publish_token = primary_client is self.vk_client

        try:
            if is_publish_token:
                await self._enforce_publish_token_rate_limit()
            response = await self._invoke(primary_client, method, params)
            return response, ("publish-token" if is_publish_token else "community-token")
        except _VKApiCallError as e:
            if not is_publish_token and e.code in self._COMMUNITY_FALLBACK_CODES:
                logger.info(
                    "wall publish via community-token failed (code %s on %s), "
                    "retrying via publish-token",
                    e.code,
                    method,
                )
                await self._enforce_publish_token_rate_limit()
                response = await self._invoke(self.vk_client, method, params)
                return response, "community-fallback-publish"
            raise Exception(f"VK API error: {e.message}") from e

    @classmethod
    async def _enforce_publish_token_rate_limit(cls) -> None:
        """Throttle calls that go through the publish-token (VALSTAN).

        Shared across all VKPublisher instances in the process via class-vars,
        because every VKPublisher() in this codebase uses the same publish-token
        from `get_publish_token()`. Multiple parallel Celery tasks would each
        wait their turn — currently the worker runs `-c 1` so this is just a
        time gate, not a contention lock, but the asyncio.Lock makes it correct
        if concurrency is bumped later.
        """
        async with cls._publish_token_lock:
            now = datetime.now()
            last = cls._last_publish_token_call
            if last is not None:
                elapsed = (now - last).total_seconds()
                wait = cls.GLOBAL_PUBLISH_INTERVAL_SECONDS - elapsed
                if wait > 0:
                    logger.debug(
                        "Publish-token global rate-limit: waiting %.2fs",
                        wait,
                    )
                    await asyncio.sleep(wait)
            cls._last_publish_token_call = datetime.now()

    async def _invoke(self, target_client, method: str, params: Dict[str, Any]) -> Dict:
        """Single VK API call; raises _VKApiCallError on VK error payload."""
        if hasattr(target_client, "api_call"):
            import asyncio
            import inspect

            api_call_method = getattr(target_client, "api_call")
            if inspect.iscoroutinefunction(api_call_method):
                response = await api_call_method(method, params)
            else:
                response = await asyncio.get_event_loop().run_in_executor(
                    None, api_call_method, method, params
                )
        elif hasattr(target_client, "method"):
            response = target_client.method(method, params)
        else:
            raise NotImplementedError("VK client doesn't support API calls")

        if isinstance(response, dict) and "error" in response:
            err = response.get("error", {}) or {}
            message = str(err.get("error_msg") or "Unknown error")
            code = int(err.get("error_code") or 0)
            if code == 0:
                # VKClient.api_call historically returned {'error': {'error_msg': str(ApiError)}}
                # without an explicit error_code. The string form starts with "[NN] ..." —
                # parse it as a fallback so the retry-on-15/27 logic still works.
                import re

                m = re.match(r"^\[(\d+)\]", message)
                if m:
                    code = int(m.group(1))
            raise _VKApiCallError(code=code, message=message)

        if isinstance(response, dict) and "response" in response:
            return response["response"]
        return response

    async def _enforce_rate_limit(self, group_id: int):
        """Enforce minimum interval between posts to same group."""
        last_post_time = self._last_post_time.get(group_id)

        if last_post_time:
            elapsed = (datetime.now() - last_post_time).total_seconds()
            if elapsed < self.POST_INTERVAL_SECONDS:
                wait_time = self.POST_INTERVAL_SECONDS - elapsed
                logger.info(f"⏳ Rate limiting: waiting {wait_time:.1f}s before next post")
                await asyncio.sleep(wait_time)

    def is_test_mode(self) -> bool:
        """Check if running in test polygon mode."""
        return self.test_polygon_mode

    def get_posts_remaining_today(self, group_id: int) -> int:
        """
        Get remaining posts that can be published today.

        This is a simplified check - in production would track via DB.
        """
        # Simplified - would need actual tracking in production
        return self.POSTS_PER_DAY_LIMIT

    async def get_group_info(self, group_id: int) -> Optional[Dict[str, Any]]:
        """Fetch VK group info via groups.getById.

        Returns dict with id/name/screen_name/type/url, or None on failure.
        Sign of group_id is irrelevant — VK groups.getById expects positive id.

        VK API contract: param is ``group_ids`` (plural — comma-separated list
        of ids or a single id as string). Old singular ``group_id`` is rejected
        with error 100 since the v5.x deprecation. Zero / negative-after-abs
        ``group_id=0`` is short-circuited to None to avoid a guaranteed VK
        validation failure when ``VK_TEST_GROUP_ID`` env-var is unset.
        """
        positive_id = abs(int(group_id))
        if not positive_id:
            return None
        try:
            response = await self._invoke(
                self.vk_client, "groups.getById", {"group_ids": str(positive_id)}
            )
        except Exception as e:
            logger.error("groups.getById(%s) failed: %s", positive_id, e)
            return None

        items = response
        if isinstance(response, dict) and "groups" in response:
            items = response["groups"]
        if not items:
            return None

        group = items[0]
        return {
            "id": group["id"],
            "name": group["name"],
            "screen_name": group["screen_name"],
            "type": group["type"],
            "url": f"https://vk.com/{group['screen_name']}",
        }

    @staticmethod
    def get_target_group_id(region_code: str, mode: str = "test") -> Optional[int]:
        """Resolve target group id for a region.

        Args:
            region_code: region code like ``mi``, ``nolinsk``.
            mode: ``test`` → test polygon group, anything else → region's main group.

        Returns None if the region is unknown.
        """
        from modules.region_config import RegionConfigManager

        if mode == "test":
            return RegionConfigManager.get_main_group_id("test")
        return RegionConfigManager.get_main_group_id(region_code)

    async def publish_aggregated_post(
        self, digest: "AggregatedPost", group_id: int
    ) -> Dict[str, Any]:
        """Publish an ``AggregatedPost`` produced by NewsAggregator.

        Thin wrapper over ``publish_digest`` that pulls ``digest.aggregated_text``
        and emits a couple of useful log lines. Attachments are not extracted —
        ``publish_digest`` accepts them explicitly if a caller needs media.
        """
        try:
            text = digest.aggregated_text
        except AttributeError as e:
            return {"success": False, "error": f"digest missing aggregated_text: {e}"}

        logger.info(
            "Publishing aggregated post to %s (sources=%s, views=%s, likes=%s)",
            group_id,
            getattr(digest, "sources_count", "?"),
            getattr(digest, "total_views", "?"),
            getattr(digest, "total_likes", "?"),
        )

        return await self.publish_digest(group_id=group_id, text=text)

    @staticmethod
    def _normalize_group_owner_id(group_id: int) -> int:
        """
        Normalize region VK group ID to owner_id format expected by wall.post/wall.repost.

        In DB and migration scripts IDs are sometimes stored as positive numbers.
        VK wall methods for groups require negative owner_id.
        """
        gid = int(group_id)
        return -abs(gid)


class _VKApiCallError(Exception):
    def __init__(self, code: int, message: str):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
