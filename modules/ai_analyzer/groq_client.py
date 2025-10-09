"""
Groq AI Client for post analysis
Free and fast alternative to local AI models
"""
import httpx
from typing import Dict, Any, Optional, List
import logging
import json

logger = logging.getLogger(__name__)


class GroqClient:
    """Client for Groq Cloud AI API"""
    
    def __init__(self, api_key: Optional[str] = None):
        """
        Initialize Groq client
        
        Args:
            api_key: Groq API key (get free at https://console.groq.com)
        """
        self.api_key = api_key or "GROQ_API_KEY_HERE"  # Will be set from config
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"
        self.model = "llama-3.1-8b-instant"  # Fast and efficient model
        
    async def analyze_post(
        self,
        text: str,
        categories: List[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze post text using AI
        
        Args:
            text: Post text to analyze
            categories: List of possible categories
            
        Returns:
            Dictionary with analysis results
        """
        if categories is None:
            categories = ['novost', 'reklama', 'admin', 'kultura', 'sport', 'sosed']
        
        # Create prompt
        prompt = f"""Проанализируй этот пост из социальной сети и определи:

1. Категория (одна из: {', '.join(categories)})
2. Релевантность для новостной ленты (0-100, где 100 = очень важная новость)
3. Спам? (да/нет)

Пост:
{text[:500]}

Ответь строго в формате JSON:
{{"category": "...", "relevance": 0-100, "is_spam": true/false, "reason": "..."}}"""
        
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.api_url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json"
                    },
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "user", "content": prompt}
                        ],
                        "temperature": 0.3,
                        "max_tokens": 200
                    }
                )
                
                if response.status_code == 200:
                    data = response.json()
                    content = data['choices'][0]['message']['content']
                    
                    # Try to parse JSON response
                    try:
                        result = json.loads(content)
                        return result
                    except json.JSONDecodeError:
                        # Fallback if AI didn't return valid JSON
                        return {
                            'category': 'novost',
                            'relevance': 50,
                            'is_spam': False,
                            'reason': 'Failed to parse AI response',
                            'raw_response': content
                        }
                else:
                    logger.error(f"Groq API error: {response.status_code}")
                    return self._fallback_analysis(text)
                    
        except Exception as e:
            logger.error(f"Error calling Groq API: {e}")
            return self._fallback_analysis(text)
    
    def _fallback_analysis(self, text: str) -> Dict[str, Any]:
        """
        Fallback analysis if API fails
        Simple keyword-based categorization
        """
        text_lower = text.lower()
        
        # Simple keyword detection
        spam_keywords = ['продам', 'куплю', 'продаю', 'продаётся', 'продается', 
                         'закажи', 'заказать', 'скидка', 'акция']
        admin_keywords = ['администрация', 'постановление', 'глава', 'губернатор']
        kultura_keywords = ['концерт', 'выставка', 'библиотека', 'музей']
        sport_keywords = ['соревнования', 'турнир', 'спорт', 'матч']
        
        category = 'novost'  # default
        relevance = 50
        is_spam = False
        
        # Check spam
        if any(keyword in text_lower for keyword in spam_keywords):
            category = 'reklama'
            relevance = 30
            is_spam = True
        elif any(keyword in text_lower for keyword in admin_keywords):
            category = 'admin'
            relevance = 70
        elif any(keyword in text_lower for keyword in kultura_keywords):
            category = 'kultura'
            relevance = 60
        elif any(keyword in text_lower for keyword in sport_keywords):
            category = 'sport'
            relevance = 65
        
        return {
            'category': category,
            'relevance': relevance,
            'is_spam': is_spam,
            'reason': 'Fallback keyword-based analysis'
        }
    
    async def batch_analyze(
        self,
        posts: List[Dict[str, str]]
    ) -> List[Dict[str, Any]]:
        """
        Analyze multiple posts in batch
        
        Args:
            posts: List of dicts with 'id' and 'text'
            
        Returns:
            List of analysis results
        """
        results = []
        
        for post in posts:
            result = await self.analyze_post(post['text'])
            result['post_id'] = post['id']
            results.append(result)
        
        return results

