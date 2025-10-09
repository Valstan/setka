"""
AI Post Analyzer - analyzes VK posts for relevance and categorization
Uses Groq API (free tier) for AI analysis
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from database.connection import AsyncSessionLocal
from database.models import Post, Filter
from modules.ai_analyzer.groq_client import GroqClient

logger = logging.getLogger(__name__)


class PostAnalyzer:
    """Analyzes posts using AI and filters"""
    
    def __init__(self, groq_api_key: Optional[str] = None):
        """
        Initialize Post Analyzer
        
        Args:
            groq_api_key: Groq API key (optional, can use fallback)
        """
        self.groq_client = GroqClient(api_key=groq_api_key) if groq_api_key else None
        self.use_fallback = not groq_api_key
        
    async def analyze_post(
        self,
        post: Post,
        session: AsyncSession
    ) -> Dict[str, Any]:
        """
        Analyze single post
        
        Args:
            post: Post object from database
            session: Database session
            
        Returns:
            Analysis results
        """
        if not post.text:
            return {'error': 'No text to analyze'}
        
        logger.info(f"Analyzing post {post.id}: {post.text[:50]}...")
        
        # Check blacklist filters first
        is_spam, spam_reason = await self._check_filters(post.text, session)
        
        if is_spam:
            result = {
                'category': 'reklama',
                'relevance': 0,
                'is_spam': True,
                'reason': spam_reason,
                'score': 0
            }
        else:
            # Use AI or fallback
            if self.groq_client and not self.use_fallback:
                try:
                    result = await self.groq_client.analyze_post(post.text)
                except Exception as e:
                    logger.warning(f"Groq API failed, using fallback: {e}")
                    result = self.groq_client._fallback_analysis(post.text)
            else:
                # Use fallback keyword-based analysis
                result = self._keyword_analysis(post.text)
        
        # Calculate final score
        score = self._calculate_score(result, post)
        result['score'] = score
        
        # Update post with analysis
        post.ai_category = result.get('category', 'novost')
        post.ai_relevance = result.get('relevance', 50)
        post.ai_score = score
        post.ai_analyzed = True
        post.ai_analysis_date = datetime.utcnow()
        post.is_spam = result.get('is_spam', False)
        
        # Update status
        if score >= 70:
            post.status = 'approved'
        elif score >= 40:
            post.status = 'analyzed'
        else:
            post.status = 'rejected'
        
        return result
    
    async def _check_filters(
        self,
        text: str,
        session: AsyncSession
    ) -> tuple[bool, Optional[str]]:
        """
        Check text against blacklist filters
        
        Returns:
            (is_spam, reason)
        """
        # Get active blacklist filters
        result = await session.execute(
            select(Filter).where(
                and_(
                    Filter.type == 'blacklist_word',
                    Filter.is_active == True
                )
            )
        )
        filters = result.scalars().all()
        
        text_lower = text.lower()
        
        for filter_obj in filters:
            if filter_obj.pattern.lower() in text_lower:
                return True, f"Blacklist: {filter_obj.pattern}"
        
        return False, None
    
    def _keyword_analysis(self, text: str) -> Dict[str, Any]:
        """
        Simple keyword-based analysis (fallback)
        """
        text_lower = text.lower()
        
        # Keywords for categories
        categories_keywords = {
            'reklama': ['продам', 'куплю', 'продаю', 'продаётся', 'продается', 
                       'закажи', 'заказать', 'скидка', 'акция', 'цена', 'руб'],
            'admin': ['администрация', 'постановление', 'глава', 'губернатор',
                     'решение', 'совет', 'депутат'],
            'kultura': ['концерт', 'выставка', 'библиотека', 'музей', 'театр',
                       'фестиваль', 'творчество'],
            'sport': ['соревнования', 'турнир', 'спорт', 'матч', 'чемпионат',
                     'тренировка', 'секция'],
            'detsad': ['детский сад', 'дошкольное', 'дети', 'ребёнок', 'воспитатель'],
            'sosed': ['район', 'область', 'регион', 'соседи']
        }
        
        # Count matches for each category
        scores = {}
        for category, keywords in categories_keywords.items():
            score = sum(1 for keyword in keywords if keyword in text_lower)
            scores[category] = score
        
        # Get category with max score
        if max(scores.values()) > 0:
            category = max(scores, key=scores.get)
        else:
            category = 'novost'
        
        # Calculate relevance
        relevance = min(scores.get(category, 0) * 15 + 40, 100)
        
        # Check if spam
        is_spam = category == 'reklama' and scores['reklama'] >= 2
        
        return {
            'category': category,
            'relevance': relevance,
            'is_spam': is_spam,
            'reason': 'Keyword-based analysis'
        }
    
    def _calculate_score(
        self,
        analysis: Dict[str, Any],
        post: Post
    ) -> int:
        """
        Calculate final post score (improved based on Postopus experience)
        
        Postopus insight: VK views are the MOST important signal!
        Platform already shows what people want to read.
        
        Scoring breakdown:
        - Engagement (VK metrics): 50 points (INCREASED from 30)
        - AI Relevance: 30 points (decreased from 50)
        - Recency: 20 points
        
        Total: 100 points
        """
        if analysis.get('is_spam'):
            return 0
        
        relevance = analysis.get('relevance', 50)
        
        # Engagement score (0-50 points) - KEY METRIC!
        # Views are most important (VK shows what people want)
        views_score = min((post.views / 50) * 25, 25)  # Up to 25 points from views
        likes_score = min((post.likes / 10) * 15, 15)  # Up to 15 points from likes
        reposts_score = min((post.reposts / 3) * 10, 10)  # Up to 10 points from reposts
        
        engagement_score = views_score + likes_score + reposts_score
        
        # Bonus for highly viral content (exponential boost)
        if post.views > 500:
            engagement_score = min(engagement_score * 1.2, 50)
        if post.reposts > 10:
            engagement_score = min(engagement_score * 1.1, 50)
        
        # Recency score (0-20 points)
        if post.date_published:
            age_hours = (datetime.utcnow() - post.date_published).total_seconds() / 3600
            if age_hours < 6:
                recency_score = 20  # Super fresh
            elif age_hours < 24:
                recency_score = 18
            elif age_hours < 48:
                recency_score = 12
            elif age_hours < 72:
                recency_score = 8
            else:
                recency_score = 3
        else:
            recency_score = 10
        
        # Total score (Engagement weight increased!)
        # Old: relevance * 0.5 + stats (30) + recency (20)
        # New: relevance * 0.3 + engagement (50) + recency (20)
        total = int(
            relevance * 0.3 +  # AI relevance (30% weight)
            engagement_score +  # VK engagement (50% weight) 
            recency_score       # Time factor (20% weight)
        )
        
        return min(total, 100)
    
    async def analyze_new_posts(self, limit: int = 50) -> Dict[str, int]:
        """
        Analyze all new posts in database
        
        Args:
            limit: Maximum number of posts to analyze
            
        Returns:
            Statistics
        """
        async with AsyncSessionLocal() as session:
            # Get new posts
            result = await session.execute(
                select(Post).where(
                    Post.ai_analyzed == False
                ).limit(limit)
            )
            posts = result.scalars().all()
            
            if not posts:
                logger.info("No new posts to analyze")
                return {'analyzed': 0}
            
            logger.info(f"Analyzing {len(posts)} posts...")
            
            analyzed_count = 0
            approved_count = 0
            rejected_count = 0
            
            for post in posts:
                try:
                    analysis = await self.analyze_post(post, session)
                    
                    if post.status == 'approved':
                        approved_count += 1
                    elif post.status == 'rejected':
                        rejected_count += 1
                    
                    analyzed_count += 1
                    
                    # Small delay to avoid rate limits
                    await asyncio.sleep(0.1)
                    
                except Exception as e:
                    logger.error(f"Error analyzing post {post.id}: {e}")
            
            await session.commit()
            
            logger.info(f"Analysis complete: {analyzed_count} posts, {approved_count} approved, {rejected_count} rejected")
            
            return {
                'analyzed': analyzed_count,
                'approved': approved_count,
                'rejected': rejected_count
            }

