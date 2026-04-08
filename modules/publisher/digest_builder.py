"""
Digest Builder - Assembles multiple posts into a single VK digest post

Migrated from old_postopus bin/rw/posting_post.py
Builds formatted digests with headers, attribution, hashtags, and media attachments.
"""
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field
from datetime import datetime

from utils.post_utils import (
    lip_of_post,
    url_of_post,
    extract_source_attribution,
    truncate_text,
    format_post_stats,
)
from utils.vk_attachments import (
    extract_vk_attachments,
    build_attachments_list,
    count_attachments,
)


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
    MAX_ATTACHMENTS = 10    # VK wall.post media limit
    
    # Default header
    DEFAULT_HEADER = "📰 Дайджест новостей"
    
    def __init__(
        self,
        header: str = "",
        hashtags: List[str] = None,
        local_hashtag: str = "",
        max_text_length: int = MAX_TEXT_LENGTH,
        repost_mode: bool = False,
    ):
        """
        Args:
            header: Digest header text
            hashtags: List of theme hashtags
            local_hashtag: Local region hashtag
            max_text_length: Maximum text length
            repost_mode: True = VK repost, False = copy with attribution
        """
        self.header = header or self.DEFAULT_HEADER
        self.hashtags = hashtags or []
        self.local_hashtag = local_hashtag
        self.max_text_length = max_text_length
        self.repost_mode = repost_mode
    
    def build_digest(
        self,
        posts: List[Dict[str, Any]],
        group_names: Dict[str, str] = None,
    ) -> DigestResult:
        """
        Build digest from list of posts.
        
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
        all_attachments = []
        posts_included = []
        
        # Add header
        digest_parts.append(self.header)
        digest_parts.append("")  # Empty line
        
        for post_data in sorted_posts:
            # Extract post info
            owner_id = post_data.get('owner_id', post_data.get('from_id', 0))
            post_id = post_data.get('id', 0)
            post_text = post_data.get('text', '') or ''
            
            # Get group name
            community_vk_id = post_data.get('community_vk_id', owner_id)
            group_name = group_names.get(str(community_vk_id), group_names.get(str(owner_id), ''))
            
            # Create post entry
            if self.repost_mode:
                # Repost mode: just add repost link
                digest_parts.append(url_of_post(owner_id, post_id))
            else:
                # Copy mode: add attribution + text
                attribution = extract_source_attribution(post_data, group_name)
                
                # Truncate text if needed (leave room for other posts)
                available_length = self._available_length(digest_parts, len(posts_included) + 1)
                truncated_text = truncate_text(post_text, available_length)
                
                digest_parts.append(attribution)
                if truncated_text:
                    digest_parts.append(truncated_text)
                
            digest_parts.append("")  # Separator
            
            # Track lip
            lip = lip_of_post(owner_id, post_id)
            posts_included.append(lip)
            
            # Extract attachments
            attachments = extract_vk_attachments(post_data)
            all_attachments.append(attachments)
        
        # Add hashtags at the end
        hashtag_text = self._build_hashtag_text()
        if hashtag_text:
            digest_parts.append(hashtag_text)
        
        # Join all parts
        full_text = "\n".join(digest_parts)
        
        # Truncate if exceeds limit
        max_exceeded = False
        if len(full_text) > self.max_text_length:
            full_text = truncate_text(full_text, self.max_text_length, "\n\n...")
            max_exceeded = True
        
        # Build attachments list (max 10)
        flat_attachments = []
        for attachments in all_attachments:
            flat_attachments.extend(build_attachments_list(attachments))
            if len(flat_attachments) >= self.MAX_ATTACHMENTS:
                flat_attachments = flat_attachments[:self.MAX_ATTACHMENTS]
                break
        
        max_attachments_exceeded = len(flat_attachments) > self.MAX_ATTACHMENTS
        
        return DigestResult(
            text=full_text,
            attachments_list=flat_attachments[:self.MAX_ATTACHMENTS],
            post_count=len(posts_included),
            total_length=len(full_text),
            posts_included=posts_included,
            max_length_exceeded=max_exceeded,
            max_attachments_exceeded=max_attachments_exceeded,
        )
    
    def _sort_by_popularity(self, posts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort posts by popularity score (descending)."""
        from utils.post_utils import post_popularity
        
        def get_score(post_data):
            return post_popularity(
                views=post_data.get('views', 0),
                likes=post_data.get('likes', {}).get('count', 0),
                comments=post_data.get('comments', {}).get('count', 0),
                reposts=post_data.get('reposts', {}).get('count', 0),
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
        formatted = [f"#{tag}" if not tag.startswith('#') else tag for tag in hashtags]
        
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
        header_length = len(self.header) + 2  # +2 for newlines
        hashtag_length = len(self._build_hashtag_text()) + 2
        available = self.max_text_length - header_length - hashtag_length
        
        # Each post needs: text + attribution + newline
        post_overhead = 50  # Attribution + spacing
        posts_by_text = available // (avg_post_length + post_overhead)
        
        # Attachment capacity
        posts_by_attachments = self.MAX_ATTACHMENTS // max(avg_attachments, 1)
        
        # Take minimum
        return min(posts_by_text, posts_by_attachments)


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
            tag = hashtag if hashtag.startswith('#') else f"#{hashtag}"
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
