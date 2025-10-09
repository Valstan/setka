"""
Duplication detector - checks if content is duplicate
"""
import logging
from typing import Optional, Dict, Any, List
from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from database.models import Post
from .fingerprints import (
    create_lip_fingerprint,
    create_media_fingerprint,
    create_text_fingerprint,
    create_text_core_fingerprint
)

logger = logging.getLogger(__name__)


class DuplicationDetector:
    """
    Detects duplicate content using multiple fingerprint methods
    
    Based on Postopus proven patterns:
    1. Structural (lip) - fastest, 100% accurate for exact posts
    2. Media - detects same photos/videos
    3. Text core - detects semantic duplicates (core innovation)
    """
    
    def __init__(self, session: AsyncSession):
        """
        Initialize detector
        
        Args:
            session: Database session
        """
        self.session = session
    
    async def check_duplicate(
        self,
        owner_id: int,
        post_id: int,
        text: Optional[str] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
        region_id: Optional[int] = None,
        check_methods: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """
        Check if post is duplicate using multiple methods
        
        Args:
            owner_id: VK owner_id
            post_id: VK post_id
            text: Post text (optional, for text-based checks)
            attachments: Post attachments (optional, for media checks)
            region_id: Region ID (optional, limit search to region)
            check_methods: List of methods to use. Options:
                - 'lip' (structural)
                - 'media' (photos/videos)
                - 'text' (full text)
                - 'core' (text core - recommended)
                Default: all methods
                
        Returns:
            Dictionary with results:
            - is_duplicate: bool
            - duplicate_type: str ('lip', 'media', 'text', 'core', None)
            - duplicate_post_id: int or None
            - confidence: float (0.0 to 1.0)
        """
        if check_methods is None:
            check_methods = ['lip', 'media', 'core']  # Default checks
        
        # Check 1: Structural duplicate (lip)
        if 'lip' in check_methods:
            result = await self._check_lip_duplicate(owner_id, post_id)
            if result['is_duplicate']:
                logger.info(f"Structural duplicate found: {owner_id}_{post_id}")
                return result
        
        # Check 2: Media duplicate
        if 'media' in check_methods and attachments:
            result = await self._check_media_duplicate(attachments, region_id)
            if result['is_duplicate']:
                logger.info(f"Media duplicate found: {owner_id}_{post_id}")
                return result
        
        # Check 3: Text core duplicate (most important for semantic duplicates)
        if 'core' in check_methods and text:
            result = await self._check_text_core_duplicate(text, region_id)
            if result['is_duplicate']:
                logger.info(f"Text core duplicate found: {owner_id}_{post_id}")
                return result
        
        # Check 4: Full text duplicate
        if 'text' in check_methods and text:
            result = await self._check_text_duplicate(text, region_id)
            if result['is_duplicate']:
                logger.info(f"Full text duplicate found: {owner_id}_{post_id}")
                return result
        
        # No duplicates found
        return {
            'is_duplicate': False,
            'duplicate_type': None,
            'duplicate_post_id': None,
            'confidence': 0.0
        }
    
    async def _check_lip_duplicate(
        self,
        owner_id: int,
        post_id: int
    ) -> Dict[str, Any]:
        """Check for structural duplicate using lip fingerprint"""
        lip = create_lip_fingerprint(owner_id, post_id)
        
        result = await self.session.execute(
            select(Post).where(Post.fingerprint_lip == lip).limit(1)
        )
        duplicate = result.scalar_one_or_none()
        
        if duplicate:
            return {
                'is_duplicate': True,
                'duplicate_type': 'lip',
                'duplicate_post_id': duplicate.id,
                'confidence': 1.0  # 100% confidence
            }
        
        return {'is_duplicate': False}
    
    async def _check_media_duplicate(
        self,
        attachments: List[Dict[str, Any]],
        region_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Check for media duplicate"""
        media_ids = create_media_fingerprint(attachments)
        
        if not media_ids:
            return {'is_duplicate': False}
        
        # Check if any of the media IDs already exist
        query = select(Post).where(
            Post.fingerprint_media.contains(media_ids[:1])  # Check first media
        )
        
        if region_id:
            query = query.where(Post.region_id == region_id)
        
        result = await self.session.execute(query.limit(1))
        duplicate = result.scalar_one_or_none()
        
        if duplicate:
            return {
                'is_duplicate': True,
                'duplicate_type': 'media',
                'duplicate_post_id': duplicate.id,
                'confidence': 0.95  # High confidence
            }
        
        return {'is_duplicate': False}
    
    async def _check_text_duplicate(
        self,
        text: str,
        region_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """Check for full text duplicate"""
        fingerprint = create_text_fingerprint(text)
        
        if not fingerprint:
            return {'is_duplicate': False}
        
        query = select(Post).where(Post.fingerprint_text == fingerprint)
        
        if region_id:
            query = query.where(Post.region_id == region_id)
        
        result = await self.session.execute(query.limit(1))
        duplicate = result.scalar_one_or_none()
        
        if duplicate:
            return {
                'is_duplicate': True,
                'duplicate_type': 'text',
                'duplicate_post_id': duplicate.id,
                'confidence': 0.90  # High confidence
            }
        
        return {'is_duplicate': False}
    
    async def _check_text_core_duplicate(
        self,
        text: str,
        region_id: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Check for text core duplicate
        
        This is the KEY innovation from Postopus:
        Detects semantic duplicates even when beginning/end differ
        """
        fingerprint = create_text_core_fingerprint(text)
        
        if not fingerprint:
            return {'is_duplicate': False}
        
        query = select(Post).where(Post.fingerprint_text_core == fingerprint)
        
        if region_id:
            query = query.where(Post.region_id == region_id)
        
        result = await self.session.execute(query.limit(1))
        duplicate = result.scalar_one_or_none()
        
        if duplicate:
            return {
                'is_duplicate': True,
                'duplicate_type': 'core',
                'duplicate_post_id': duplicate.id,
                'confidence': 0.85  # Good confidence
            }
        
        return {'is_duplicate': False}
    
    async def get_similar_posts(
        self,
        text: str,
        region_id: Optional[int] = None,
        limit: int = 5
    ) -> List[Post]:
        """
        Find similar posts (not exact duplicates)
        
        Useful for:
        - Finding related content
        - Aggregation candidates
        - Quality checking
        
        Args:
            text: Post text
            region_id: Filter by region
            limit: Maximum results
            
        Returns:
            List of similar posts
        """
        fingerprint = create_text_core_fingerprint(text)
        
        if not fingerprint:
            return []
        
        query = select(Post).where(Post.fingerprint_text_core == fingerprint)
        
        if region_id:
            query = query.where(Post.region_id == region_id)
        
        query = query.limit(limit)
        
        result = await self.session.execute(query)
        return list(result.scalars().all())

