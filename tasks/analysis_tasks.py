"""
Analysis tasks - AI post analysis
"""
import asyncio
import logging
from celery import Task
from celery_app import app
from datetime import datetime

from modules.ai_analyzer.analyzer import PostAnalyzer
from config.config_secure import GROQ_API_KEY

logger = logging.getLogger(__name__)


class AsyncTask(Task):
    """Base task class for async operations"""
    
    def __call__(self, *args, **kwargs):
        """Run async function in event loop"""
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(self.run_async(*args, **kwargs))
    
    async def run_async(self, *args, **kwargs):
        """Override this method in child classes"""
        raise NotImplementedError


@app.task(base=AsyncTask, bind=True, name='tasks.analysis_tasks.analyze_new_posts')
async def analyze_new_posts(self, limit: int = 50):
    """
    Analyze new posts with AI
    
    Args:
        limit: Maximum number of posts to analyze
        
    Runs every 2 minutes
    """
    logger.info(f"🤖 Starting AI analysis (limit: {limit})...")
    
    try:
        # Initialize analyzer
        analyzer = PostAnalyzer(groq_api_key=GROQ_API_KEY)
        
        # Analyze posts
        results = await analyzer.analyze_new_posts(limit=limit)
        
        logger.info(
            f"✅ Analysis completed: {results['analyzed']} posts, "
            f"{results.get('approved', 0)} approved, "
            f"{results.get('rejected', 0)} rejected"
        )
        
        return {
            'status': 'success',
            'timestamp': datetime.utcnow().isoformat(),
            'analyzed': results['analyzed'],
            'approved': results.get('approved', 0),
            'rejected': results.get('rejected', 0)
        }
        
    except Exception as e:
        logger.error(f"❌ Analysis failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }


@app.task(base=AsyncTask, bind=True, name='tasks.analysis_tasks.reanalyze_post')
async def reanalyze_post(self, post_id: int):
    """
    Re-analyze specific post
    
    Args:
        post_id: Post ID to re-analyze
    """
    logger.info(f"🤖 Re-analyzing post {post_id}...")
    
    try:
        from database.connection import AsyncSessionLocal
        from database.models import Post
        from sqlalchemy import select
        
        analyzer = PostAnalyzer(groq_api_key=GROQ_API_KEY)
        
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Post).where(Post.id == post_id)
            )
            post = result.scalar_one_or_none()
            
            if not post:
                return {'error': f'Post {post_id} not found'}
            
            # Analyze
            analysis = await analyzer.analyze_post(post, session)
            await session.commit()
            
            logger.info(f"✅ Post {post_id} re-analyzed: {analysis}")
            
            return {
                'status': 'success',
                'post_id': post_id,
                'category': analysis.get('category'),
                'score': analysis.get('score'),
                'new_status': post.status
            }
            
    except Exception as e:
        logger.error(f"❌ Re-analysis failed: {e}")
        return {
            'status': 'failed',
            'error': str(e)
        }

