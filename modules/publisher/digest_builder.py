"""
Digest Builder - Assembles multiple posts into a single VK digest post

Migrated from old_postopus bin/rw/posting_post.py
Builds formatted digests with headers, attribution, hashtags, and media attachments.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from utils.post_utils import extract_source_attribution, lip_of_post
from utils.text_utils import truncate_text
from utils.vk_attachments import build_attachments_list, extract_vk_attachments


@dataclass
class DigestPost:
    """A single post included in the digest"""

    post_data: Dict[str, Any]
    source_attribution: str
    text: str
    attachments: Dict[str, List[Dict]]
    lip: str
    popularity_score: float = 0.0


@dataclass
class DigestResult:
    """Result of digest building"""

    text: str
    attachments_list: List[str]  # VK API attachment strings
    post_count: int
    total_length: int
    posts_included: List[str]  # lip strings
    max_length_exceeded: bool = False
    max_attachments_exceeded: bool = False


class DigestBuilder:
    """
    Builds digest posts from multiple source posts.

    Migrated from old_postopus posting_post() function.

    Features:
    - Adds header from region config (zagolovki)
    - Adds source attribution: @url (group_name)
    - Adds hashtags: #hashtag #local_region
    - Respects VK limits (4096 chars, 10 media items)
    - Sorts posts by popularity
    - Tracks lip hashes to prevent re-publishing
    """

    # VK limits
    MAX_TEXT_LENGTH = 4096  # VK post text limit
    MAX_ATTACHMENTS = 10  # VK wall.post media limit
    MAX_POSTS_PER_DIGEST = 3  # Maximum number of posts in a single digest

    # Default header
    DEFAULT_HEADER = "📰 Дайджест новостей"

    # Post separator emoji (from old_postopus style)
    POST_MARKER = "✍ "

    def __init__(
        self,
        header: str = "",
        hashtags: List[str] = None,
        local_hashtag: str = "",
        max_text_length: int = MAX_TEXT_LENGTH,
        repost_mode: bool = False,
        max_posts_per_digest: Optional[int] = None,
    ):
        """
        Args:
            header: Digest header text
            hashtags: List of theme hashtags
            local_hashtag: Local region hashtag
            max_text_length: Maximum text length
            repost_mode: True = VK repost, False = copy with attribution
            max_posts_per_digest: Сколько новостей максимум в одном дайджесте (из настроек региона)
        """
        # Пустая строка = без заголовка (например траурный дайджест); None = дефолтный заголовок
        if header is None:
            self.header = self.DEFAULT_HEADER
        else:
            self.header = header
        self.hashtags = hashtags or []
        self.local_hashtag = local_hashtag
        self.max_text_length = max_text_length
        self.repost_mode = repost_mode
        self.max_posts_per_digest = (
            int(max_posts_per_digest)
            if max_posts_per_digest is not None
            else self.MAX_POSTS_PER_DIGEST
        )
        self.max_posts_per_digest = max(1, min(self.max_posts_per_digest, 10))

    def build_digest(
        self,
        posts: List[Dict[str, Any]],
        group_names: Dict[str, str] = None,
    ) -> DigestResult:
        """
        Build digest from list of posts.

        Format (old_postopus style):
            {HEADER}

            ✍ {post_text_1}

            @https://vk.com/wall-... (source_name)

            ✍ {post_text_2}

            @https://vk.com/wall-... (source_name)

            #hashtag1 #hashtag2

        IMPORTANT: Posts that don't fit entirely are SKIPPED (not truncated).
        They will be included in the next iteration.

        Args:
            posts: List of VK post data dicts
            group_names: Dict mapping community_vk_id -> group display name

        Returns:
            DigestResult with formatted text and attachments
        """
        if group_names is None:
            group_names = {}

        # Sort posts by popularity
        sorted_posts = self._sort_by_popularity(posts)

        # Build digest text and collect attachments
        digest_parts = []
        flat_attachments = []
        posts_included = []

        # Заголовок (если задан непустой)
        if self.header and str(self.header).strip():
            digest_parts.append(str(self.header).strip())
            digest_parts.append("")  # пустая строка после заголовка

        # Calculate static content that must always fit
        hashtag_text = self._build_hashtag_text()
        hashtag_overhead = len(hashtag_text) + 2 if hashtag_text else 2  # +2 for newlines

        for post_data in sorted_posts:
            # Stop if we've reached max posts
            if len(posts_included) >= self.max_posts_per_digest:
                break

            # Extract post info
            owner_id = post_data.get("owner_id", post_data.get("from_id", 0))
            post_id = post_data.get("id", 0)
            post_text = post_data.get("text", "") or ""

            # Skip posts with no text (problem 3)
            if not post_text.strip():
                continue

            # Get group name (ключи в group_names — по abs(owner_id))
            community_vk_id = post_data.get("community_vk_id", owner_id)
            try:
                aid = abs(int(community_vk_id if community_vk_id is not None else owner_id))
            except (TypeError, ValueError):
                aid = abs(int(owner_id)) if owner_id else 0
            group_name = group_names.get(str(aid), "") if aid else ""

            # Build the complete post entry
            post_entry = self._format_post_entry(
                post_data, post_text, owner_id, post_id, group_name
            )

            # Extract attachments to check if they fit
            attachments = extract_vk_attachments(post_data)
            post_attachments = build_attachments_list(attachments)

            # Calculate total length if we add this post
            current_length = sum(len(part) for part in digest_parts)
            new_total = current_length + len(post_entry) + hashtag_overhead

            # Check if the FULL post fits (no truncation allowed)
            if new_total > self.max_text_length:
                # Skip this post — it will be included in the next iteration
                continue

            # Check if attachments fit (problem 5: if text fits but media doesn't — skip)
            current_attachment_count = len(flat_attachments)
            remaining_attachment_slots = self.MAX_ATTACHMENTS - current_attachment_count
            if len(post_attachments) > remaining_attachment_slots and post_attachments:
                # Post has media but no room — skip
                continue

            # Add the post
            digest_parts.append(post_entry)
            digest_parts.append("")  # Empty line separator

            # Track lip
            lip = lip_of_post(owner_id, post_id)
            posts_included.append(lip)

            # Collect attachments
            flat_attachments.extend(post_attachments)

        # If no post made it through the loop, return an empty result so callers
        # never publish a "digest" that's just a header + hashtags. All filtering
        # paths (no text, doesn't fit, attachments don't fit) end up here.
        if not posts_included:
            return DigestResult(
                text="",
                attachments_list=[],
                post_count=0,
                total_length=0,
                posts_included=[],
                max_length_exceeded=False,
                max_attachments_exceeded=False,
            )

        # Add hashtags at the end
        if hashtag_text:
            digest_parts.append(hashtag_text)

        # Join all parts — NO final truncation
        full_text = "\n".join(digest_parts)

        # Truncate attachments to VK limit
        if len(flat_attachments) > self.MAX_ATTACHMENTS:
            flat_attachments = flat_attachments[: self.MAX_ATTACHMENTS]

        max_attachments_exceeded = len(flat_attachments) > self.MAX_ATTACHMENTS

        return DigestResult(
            text=full_text,
            attachments_list=flat_attachments[: self.MAX_ATTACHMENTS],
            post_count=len(posts_included),
            total_length=len(full_text),
            posts_included=posts_included,
            max_length_exceeded=False,  # No truncation — posts that don't fit are skipped
            max_attachments_exceeded=max_attachments_exceeded,
        )

    def _format_post_entry(
        self,
        post_data: Dict[str, Any],
        post_text: str,
        owner_id: int,
        post_id: int,
        group_name: str,
    ) -> str:
        """
        Format a single post entry with ✍ marker, text, and source attribution.

        Format:
            ✍ {post_text}

            @https://vk.com/wall{owner_id}_{post_id} (group_name)

        Args:
            post_data: Raw post data dict
            post_text: Post text content
            owner_id: VK owner_id
            post_id: VK post ID
            group_name: Source group display name

        Returns:
            Formatted post entry string
        """
        parts = []

        # Post marker + text
        if post_text:
            parts.append(f"{self.POST_MARKER}{post_text}")
        else:
            # Text-only post with marker
            parts.append(f"{self.POST_MARKER}[без текста]")

        # Empty line between text and attribution only when attribution is present
        if not post_data.get("hide_attribution"):
            parts.append("")
            attribution = extract_source_attribution(post_data, group_name)
            parts.append(attribution)

        return "\n".join(parts)

    def _sort_by_popularity(self, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort posts by popularity score (descending)."""
        from utils.post_utils import post_popularity

        def get_score(post_data):
            return post_popularity(
                views=(
                    post_data.get("views", {}).get("count", 0)
                    if isinstance(post_data.get("views"), dict)
                    else post_data.get("views", 0)
                ),
                likes=post_data.get("likes", {}).get("count", 0),
                comments=post_data.get("comments", {}).get("count", 0),
                reposts=post_data.get("reposts", {}).get("count", 0),
            )

        return sorted(posts, key=get_score, reverse=True)

    def _available_length(self, current_parts: List[str], post_number: int) -> int:
        """Calculate available length for current post."""
        current_length = sum(len(part) for part in current_parts)

        # Estimate remaining length needed
        # Reserve some space for hashtags and future posts
        reserved = 200  # Hashtags + spacing
        remaining = self.max_text_length - current_length - reserved

        # Don't let single post take more than 40% of space
        max_per_post = int(self.max_text_length * 0.4)

        return min(remaining, max_per_post)

    def _build_hashtag_text(self) -> str:
        """Build hashtag string for digest."""
        hashtags = []

        # Add theme hashtags
        hashtags.extend(self.hashtags)

        # Add local hashtag
        if self.local_hashtag:
            hashtags.append(self.local_hashtag)

        if not hashtags:
            return ""

        # Format as hashtags
        formatted = [f"#{tag}" if not tag.startswith("#") else tag for tag in hashtags]

        return " ".join(formatted)

    def estimate_post_capacity(
        self,
        avg_post_length: int = 200,
        avg_attachments: int = 1,
    ) -> int:
        """
        Estimate how many posts can fit in digest.

        Args:
            avg_post_length: Average post text length
            avg_attachments: Average attachments per post

        Returns:
            Estimated number of posts
        """
        # Text capacity
        header_length = (
            (len(str(self.header).strip()) + 2) if (self.header and str(self.header).strip()) else 0
        )
        hashtag_length = len(self._build_hashtag_text()) + 2
        available = self.max_text_length - header_length - hashtag_length

        # Each post needs: text + attribution + newline
        post_overhead = 50  # Attribution + spacing
        posts_by_text = available // (avg_post_length + post_overhead)

        # Attachment capacity
        posts_by_attachments = self.MAX_ATTACHMENTS // max(avg_attachments, 1)

        # Take minimum
        return min(posts_by_text, posts_by_attachments, self.max_posts_per_digest)


class TextOnlyDigestBuilder(DigestBuilder):
    """
    Builds text-only digest (no media attachments).

    Migrated from old_postopus post_bezfoto() function.
    Used for advertising digests where images aren't needed.
    """

    def build_digest(
        self,
        posts: List[Dict[str, Any]],
        group_names: Dict[str, str] = None,
    ) -> DigestResult:
        """Build text-only digest."""
        # Use parent build but strip attachments
        result = super().build_digest(posts, group_names)

        # Clear attachments
        result.attachments_list = []
        result.max_attachments_exceeded = False

        return result

    def build_bezfoto_digest(
        self,
        text_items: List[str],
        header: str = "",
        hashtag: str = "",
    ) -> DigestResult:
        """
        Build digest from text-only items (bezfoto).

        Args:
            text_items: List of text items to include
            header: Digest header
            hashtag: Single hashtag for digest

        Returns:
            DigestResult
        """
        parts = []

        # Add header
        if header:
            parts.append(header)
            parts.append("")

        # Add text items (limit to 15 as in old_postopus)
        for i, item in enumerate(text_items[:15]):
            parts.append(f"{i+1}. {item}")
            parts.append("")

        # Add hashtag
        if hashtag:
            tag = hashtag if hashtag.startswith("#") else f"#{hashtag}"
            parts.append(tag)

        # Join and truncate
        full_text = "\n".join(parts)
        if len(full_text) > self.max_text_length:
            full_text = truncate_text(full_text, self.max_text_length, "\n\n...")

        return DigestResult(
            text=full_text,
            attachments_list=[],
            post_count=len(text_items[:15]),
            total_length=len(full_text),
            posts_included=[],
        )
