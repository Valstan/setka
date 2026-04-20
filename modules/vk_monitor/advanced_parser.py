"""
Advanced VK Parser - Main parsing orchestration module

Migrated from old_postopus bin/control/parser.py
Core parsing logic with full filtering pipeline.

This is the heart of the parsing system - fetches posts from VK communities,
applies all filters, and returns cleaned posts ready for digest building.
"""
import logging
import random
from typing import List, Dict, Any, Optional, Set
from datetime import datetime, timezone

from modules.vk_monitor.vk_client import VKClient
from utils.post_utils import lip_of_post, clear_copy_history, post_popularity
from utils.vk_attachments import extract_vk_attachments, has_attachments
from utils.text_utils import is_advertisement, check_blacklist
from modules.deduplication.fingerprints import (
    create_text_fingerprint,
    create_text_core_fingerprint,
    create_media_fingerprint,
    text_to_rafinad,
)

logger = logging.getLogger(__name__)

# Не считать «ядро» текста для near-dup, если rafinad слишком короткий (меньше ложных срабатываний)
_MIN_RAFINAD_LEN_FOR_CORE_DEDUP = 50

# Дайджесты: не брать посты старше 72 часов с момента публикации (оригинала при репосте)
DIGEST_MAX_POST_AGE_HOURS = 72


def _post_age_hours_utc(
    post_data: Dict[str, Any],
    now_ts: Optional[float] = None,
) -> Optional[float]:
    """Возраст поста в часах по полю date (Unix UTC). None — нет валидной даты."""
    raw = post_data.get("date")
    if raw is None:
        return None
    try:
        ts = float(raw)
    except (TypeError, ValueError):
        return None
    if now_ts is None:
        now_ts = datetime.now(tz=timezone.utc).timestamp()
    return max(0.0, (now_ts - ts) / 3600.0)


class AdvancedVKParser:
    """
    Advanced VK post parser with full filtering pipeline.
    
    Migrated from old_postopus parser.py with all filtering logic:
    1. Unwrap репостов, возраст, дедуп lip (work_table + батч)
    2. Фильтры black_id, реклама, blacklist, вложения, темы
    3. Дедуп текста и медиа внутри одного прогона
    
    Returns filtered posts ready for digest building.
    """
    
    def __init__(self, vk_client: VKClient):
        """
        Args:
            vk_client: VK API client instance
        """
        self.vk_client = vk_client
        self._max_post_age_hours = float(DIGEST_MAX_POST_AGE_HOURS)
        self._min_rafinad_core = int(_MIN_RAFINAD_LEN_FOR_CORE_DEDUP)

        # Parsing statistics (stat_mode)
        self.stats = {
            'total_groups_checked': 0,
            'total_posts_scanned': 0,
            'posts_filtered_old': 0,
            'posts_filtered_duplicate_lip': 0,
            'posts_filtered_duplicate_text': 0,
            'posts_filtered_duplicate_foto': 0,
            'posts_filtered_black_id': 0,
            'posts_filtered_no_region_words': 0,
            'posts_filtered_advertisement': 0,
            'posts_filtered_no_attachments': 0,
            'posts_filtered_blacklist_text': 0,
            'posts_final_count': 0,
            'groups_with_posts': 0,
        }
    
    async def parse_posts_from_communities(
        self,
        community_ids: List[int],
        theme: str = "novost",
        region_config: Any = None,
        work_table_lip: List[str] = None,
        work_table_hash: List[str] = None,
        recent_text_fingerprints: List[str] = None,
        count_per_community: int = 20,
        shuffle_communities: bool = True,
        pipeline_settings: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Parse posts from multiple communities with full filtering.
        
        This is the main entry point, migrated from old_postopus parser().
        
        Args:
            community_ids: List of VK community IDs to scan
            theme: Theme (novost, kultura, sport, etc.)
            region_config: RegionConfig object for filtering
            work_table_lip: List of published post lips (for dedup)
            work_table_hash: List of photo hashes (for dedup)
            recent_text_fingerprints: Recent text fingerprints (for dedup)
            count_per_community: Posts to fetch per community
            shuffle_communities: Randomize community order
            pipeline_settings: Слитые настройки из digest_filters (возраст, дедуп, лимит fetch)
        
        Returns:
            List of filtered post data dicts
        """
        if work_table_lip is None:
            work_table_lip = []
        if work_table_hash is None:
            work_table_hash = []
        if recent_text_fingerprints is None:
            recent_text_fingerprints = []
        recent_text_set: Set[str] = set(recent_text_fingerprints) if recent_text_fingerprints else set()
        work_hash_set: Set[str] = set(work_table_hash or [])

        # Дедупликация внутри одного вызова parse_posts_from_communities
        self._batch_lips: Set[str] = set()
        self._batch_text_fps: Set[str] = set()
        self._batch_core_fps: Set[str] = set()
        self._batch_media_sigs: Set[str] = set()

        if pipeline_settings is None:
            self._max_post_age_hours = float(DIGEST_MAX_POST_AGE_HOURS)
            self._min_rafinad_core = int(_MIN_RAFINAD_LEN_FOR_CORE_DEDUP)
            effective_count = count_per_community
        else:
            self._max_post_age_hours = float(pipeline_settings.get("max_post_age_hours", DIGEST_MAX_POST_AGE_HOURS))
            self._min_rafinad_core = int(
                pipeline_settings.get("min_rafinad_len_core_dedup", _MIN_RAFINAD_LEN_FOR_CORE_DEDUP)
            )
            effective_count = int(pipeline_settings.get("posts_per_community_fetch", count_per_community))

        # Shuffle communities (randomize fetch order)
        if shuffle_communities:
            random.shuffle(community_ids)
        
        all_posts = []
        
        # Fetch posts from all communities
        for community_id in community_ids:
            self.stats['total_groups_checked'] += 1
            
            try:
                # Fetch posts from VK
                posts = await self._fetch_community_posts(community_id, effective_count)
                
                if not posts:
                    continue
                
                self.stats['groups_with_posts'] += 1
                
                # Process each post
                for post_data in posts:
                    self.stats['total_posts_scanned'] += 1
                    
                    # Apply full filtering pipeline
                    filtered = await self._filter_post(
                        post_data,
                        theme=theme,
                        region_config=region_config,
                        work_table_lip=work_table_lip,
                        work_hash_set=work_hash_set,
                        recent_text_fingerprints=recent_text_set,
                    )
                    
                    if filtered:
                        all_posts.append(filtered)
                
            except Exception as e:
                logger.error(f"❌ Failed to parse community {community_id}: {e}")
                continue
        
        # Sort by popularity
        all_posts.sort(
            key=lambda p: post_popularity(
                views=p.get('views', {}).get('count', 0) if isinstance(p.get('views'), dict) else p.get('views', 0),
                likes=p.get('likes', {}).get('count', 0),
                comments=p.get('comments', {}).get('count', 0),
                reposts=p.get('reposts', {}).get('count', 0),
            ),
            reverse=True,
        )
        
        self.stats['posts_final_count'] = len(all_posts)
        
        logger.info(
            f"📊 Parsing complete: {len(all_posts)} posts from "
            f"{self.stats['total_groups_checked']} groups"
        )
        
        return all_posts
    
    async def _fetch_community_posts(self, community_id: int, count: int) -> List[Dict]:
        """Fetch posts from a single VK community."""
        # Use VK client to get wall posts
        # Implementation depends on your VK client setup

        if hasattr(self.vk_client, 'get_wall_posts'):
            # VKClient is synchronous, run in thread
            import asyncio
            posts = await asyncio.to_thread(
                self.vk_client.get_wall_posts, -abs(community_id), count
            )
            # Add owner_id to each post (VK API may not include it for wall.get)
            for post in posts:
                if 'owner_id' not in post:
                    post['owner_id'] = -abs(community_id)
            return posts
        elif hasattr(self.vk_client, 'api_call'):
            response = await self.vk_client.api_call('wall.get', {
                'owner_id': -abs(community_id),  # Negative for groups
                'count': count,
            })
            return response.get('items', [])
        else:
            raise NotImplementedError("VK client doesn't support wall.get")
    
    async def _filter_post(
        self,
        post_data: Dict[str, Any],
        theme: str,
        region_config: Any,
        work_table_lip: List[str],
        work_hash_set: Set[str],
        recent_text_fingerprints: Set[str],
    ) -> Optional[Dict[str, Any]]:
        """
        Apply full filtering pipeline to a single post.

        Важно: сначала unwrap репоста, иначе lip разный у одного и того же оригинала на разных стенах.
        """
        # 1. Развернуть репост → owner_id/post_id/text от оригинала
        post_data = clear_copy_history(post_data)

        owner_id = post_data.get("owner_id", post_data.get("from_id", 0))
        post_id = post_data.get("id", 0)
        text = (post_data.get("text") or "").strip()

        # 2. Возраст публикации
        age_h = _post_age_hours_utc(post_data)
        if age_h is None:
            self.stats["posts_filtered_old"] += 1
            return None
        if age_h > self._max_post_age_hours:
            self.stats["posts_filtered_old"] += 1
            return None

        # 3. Lip: уже в дайджестах / уже отобран в этом прогоне (один оригинал = один раз)
        lip = lip_of_post(owner_id, post_id)
        if lip in work_table_lip or lip in self._batch_lips:
            self.stats["posts_filtered_duplicate_lip"] += 1
            return None

        # 4. Black ID
        if region_config and region_config.black_id:
            if abs(owner_id) in [abs(x) for x in region_config.black_id]:
                self.stats["posts_filtered_black_id"] += 1
                return None

        # 5. Advertisement filter
        is_reklama_theme = theme == "reklama"
        if is_advertisement(text, skip_for_reklama=is_reklama_theme, theme=theme):
            if not is_reklama_theme:
                self.stats["posts_filtered_advertisement"] += 1
                return None

        # 6. Blacklist text
        if region_config and region_config.delete_msg_blacklist:
            matched = check_blacklist(text, region_config.delete_msg_blacklist)
            if matched:
                self.stats["posts_filtered_blacklist_text"] += 1
                return None

        # 7. Region words filter (placeholder)
        if region_config and region_config.filter_group_by_region_words:
            community_vk_id = post_data.get("community_vk_id", owner_id)
            if str(abs(community_vk_id)) in {
                str(abs(x)) for x in region_config.filter_group_by_region_words.keys()
            }:
                pass

        # 8. No-attachments filter (for non-novost/non-reklama themes)
        if theme not in ("novost", "reklama"):
            attachments = extract_vk_attachments(post_data)
            if not has_attachments(attachments):
                self.stats["posts_filtered_no_attachments"] += 1
                return None

        # 9. Theme-specific filters
        if theme == "sosed":
            if "#новости" not in text.lower():
                return None

        # 10. Дедуп по медиа (набор id вложений в этом прогоне + известные hash из work_table)
        raw_atts = post_data.get("attachments")
        if not isinstance(raw_atts, list):
            raw_atts = []
        media_ids = create_media_fingerprint(raw_atts)
        media_sig = ",".join(sorted(media_ids)) if media_ids else ""
        if media_ids:
            if any(mid in work_hash_set for mid in media_ids):
                self.stats["posts_filtered_duplicate_foto"] += 1
                return None
            if media_sig in self._batch_media_sigs:
                self.stats["posts_filtered_duplicate_foto"] += 1
                return None

        # 11. Дедуп по тексту (полный hash и «ядро» для похожих формулировок)
        if text:
            fp = create_text_fingerprint(text)
            if fp:
                if fp in self._batch_text_fps or fp in recent_text_fingerprints:
                    self.stats["posts_filtered_duplicate_text"] += 1
                    return None
                rlen = len(text_to_rafinad(text))
                if rlen >= self._min_rafinad_core:
                    cfp = create_text_core_fingerprint(text)
                    if cfp and cfp in self._batch_core_fps:
                        self.stats["posts_filtered_duplicate_text"] += 1
                        return None

        # Регистрируем отобранный пост в батч-дедупе
        self._batch_lips.add(lip)
        if media_sig:
            self._batch_media_sigs.add(media_sig)
        if text:
            fp = create_text_fingerprint(text)
            if fp:
                self._batch_text_fps.add(fp)
                if len(text_to_rafinad(text)) >= self._min_rafinad_core:
                    cfp = create_text_core_fingerprint(text)
                    if cfp:
                        self._batch_core_fps.add(cfp)

        return post_data
    
    def get_stats(self) -> Dict[str, Any]:
        """Get parsing statistics (for stat_mode)."""
        return self.stats.copy()
    
    def reset_stats(self):
        """Reset parsing statistics."""
        for key in self.stats:
            self.stats[key] = 0
