"""
Sentiment Analyzer - анализ эмоционального тона новостей
Легковесная keyword-based реализация для русского языка

Для production можно заменить на RuBERT или другую модель
"""
import logging
import re
from typing import Dict, List, Tuple
from collections import Counter

logger = logging.getLogger(__name__)


class SentimentAnalyzer:
    """
    Анализ эмоционального тона текста
    
    Определяет:
    - Sentiment: positive, neutral, negative
    - Score: 0.0-1.0
    - Emotions: joy, sadness, anger, fear
    """
    
    # Словари ключевых слов для русского языка
    POSITIVE_WORDS = {
        # Радость, успех
        'отлично', 'замечательно', 'прекрасно', 'великолепно', 'чудесно',
        'победа', 'успех', 'достижение', 'радость', 'счастье', 'праздник',
        'поздравляем', 'поздравление', 'награда', 'призёр', 'победитель',
        'развитие', 'улучшение', 'рост', 'прогресс',
        'красиво', 'здорово', 'классно', 'супер', 'молодцы',
        # Позитивные события
        'открытие', 'новый', 'первый', 'лучший', 'передовой',
        'помощь', 'поддержка', 'благотворительность', 'спасение',
        'любовь', 'дружба', 'семья', 'дети', 'смех'
    }
    
    NEGATIVE_WORDS = {
        # Проблемы, плохие события
        'плохо', 'ужасно', 'страшно', 'опасно', 'тревожно',
        'проблема', 'беда', 'несчастье', 'трагедия', 'катастрофа',
        'авария', 'дтп', 'пожар', 'затопление', 'разрушение',
        'смерть', 'погиб', 'умер', 'скончался', 'жертва',
        'криминал', 'преступление', 'кража', 'грабёж', 'убийство',
        'болезнь', 'эпидемия', 'заражение',
        # Негативные эмоции
        'грустно', 'печально', 'жаль', 'обидно',
        'злость', 'гнев', 'ярость', 'возмущение',
        'страх', 'ужас', 'паника', 'тревога',
        # Проблемы инфраструктуры
        'закрыто', 'отменено', 'запрещено', 'остановка', 'ремонт',
        'не работает', 'поломка', 'авария'
    }
    
    NEUTRAL_WORDS = {
        # Административные термины
        'администрация', 'правительство', 'глава', 'мэр', 'губернатор',
        'заседание', 'совещание', 'постановление', 'решение',
        'объявление', 'сообщение', 'информация',
        # Нейтральные события
        'собрание', 'встреча', 'мероприятие', 'акция',
        'работа', 'услуга', 'предоставление'
    }
    
    # Эмоциональные маркеры
    JOY_MARKERS = {
        'праздник', 'торжество', 'радость', 'веселье', 'смех',
        'поздравление', 'награждение', 'успех', 'победа',
        'счастье', 'восторг'
    }
    
    SADNESS_MARKERS = {
        'грусть', 'печаль', 'скорбь', 'траур', 'память',
        'прощание', 'утрата', 'потеря', 'смерть', 'погиб'
    }
    
    ANGER_MARKERS = {
        'возмущение', 'протест', 'скандал', 'конфликт',
        'гнев', 'ярость', 'злость', 'недовольство'
    }
    
    FEAR_MARKERS = {
        'опасность', 'угроза', 'тревога', 'паника', 'страх',
        'беспокойство', 'волнение', 'риск'
    }
    
    def __init__(self):
        """Initialize sentiment analyzer"""
        logger.info("Sentiment Analyzer initialized (keyword-based)")
    
    def analyze(self, text: str) -> Dict:
        """
        Анализ sentiment текста
        
        Args:
            text: Текст для анализа
            
        Returns:
            Dict с результатами:
            - label: 'positive', 'neutral', 'negative'
            - score: 0.0-1.0 (уверенность)
            - emotions: dict с scores для emotions
        """
        if not text:
            return self._default_result()
        
        text_lower = text.lower()
        
        # Подсчёт слов каждой категории
        positive_count = sum(1 for word in self.POSITIVE_WORDS if word in text_lower)
        negative_count = sum(1 for word in self.NEGATIVE_WORDS if word in text_lower)
        neutral_count = sum(1 for word in self.NEUTRAL_WORDS if word in text_lower)
        
        # Определение sentiment
        total = positive_count + negative_count + neutral_count
        
        if total == 0:
            # Нет ключевых слов - нейтральный
            label = 'neutral'
            score = 0.5
        else:
            # Определить доминирующий sentiment
            if negative_count > positive_count and negative_count > neutral_count:
                label = 'negative'
                score = min(0.5 + (negative_count / max(total, 1)) * 0.5, 1.0)
            elif positive_count > negative_count and positive_count > neutral_count:
                label = 'positive'
                score = min(0.5 + (positive_count / max(total, 1)) * 0.5, 1.0)
            else:
                label = 'neutral'
                score = 0.5 + (neutral_count / max(total, 1)) * 0.3
        
        # Анализ эмоций
        emotions = self._analyze_emotions(text_lower)
        
        return {
            'label': label,
            'score': round(score, 2),
            'emotions': emotions,
            'word_counts': {
                'positive': positive_count,
                'negative': negative_count,
                'neutral': neutral_count
            }
        }
    
    def _analyze_emotions(self, text_lower: str) -> Dict[str, float]:
        """Анализ конкретных эмоций"""
        joy = sum(1 for word in self.JOY_MARKERS if word in text_lower)
        sadness = sum(1 for word in self.SADNESS_MARKERS if word in text_lower)
        anger = sum(1 for word in self.ANGER_MARKERS if word in text_lower)
        fear = sum(1 for word in self.FEAR_MARKERS if word in text_lower)
        
        total = joy + sadness + anger + fear
        
        if total == 0:
            return {
                'joy': 0.0,
                'sadness': 0.0,
                'anger': 0.0,
                'fear': 0.0
            }
        
        return {
            'joy': round(joy / total, 2),
            'sadness': round(sadness / total, 2),
            'anger': round(anger / total, 2),
            'fear': round(fear / total, 2)
        }
    
    def _default_result(self) -> Dict:
        """Default result for empty text"""
        return {
            'label': 'neutral',
            'score': 0.5,
            'emotions': {'joy': 0.0, 'sadness': 0.0, 'anger': 0.0, 'fear': 0.0},
            'word_counts': {'positive': 0, 'negative': 0, 'neutral': 0}
        }
    
    def analyze_batch(self, texts: List[str]) -> List[Dict]:
        """
        Анализ нескольких текстов
        
        Args:
            texts: Список текстов
            
        Returns:
            List результатов
        """
        return [self.analyze(text) for text in texts]
    
    def get_sentiment_distribution(self, results: List[Dict]) -> Dict:
        """
        Получить распределение sentiment
        
        Args:
            results: Результаты анализа
            
        Returns:
            Распределение по категориям
        """
        labels = [r['label'] for r in results]
        counter = Counter(labels)
        total = len(results)
        
        return {
            'positive': counter.get('positive', 0),
            'neutral': counter.get('neutral', 0),
            'negative': counter.get('negative', 0),
            'positive_pct': round((counter.get('positive', 0) / total) * 100, 1) if total > 0 else 0,
            'neutral_pct': round((counter.get('neutral', 0) / total) * 100, 1) if total > 0 else 0,
            'negative_pct': round((counter.get('negative', 0) / total) * 100, 1) if total > 0 else 0,
            'total': total
        }


if __name__ == "__main__":
    # Test
    analyzer = SentimentAnalyzer()
    
    print("="*60)
    print("🧪 Testing Sentiment Analyzer")
    print("="*60)
    
    test_texts = [
        "Отличные новости! В Малмыже открылся новый детский сад. Радость для родителей!",
        "Сегодня состоялось заседание администрации района по вопросу бюджета.",
        "Трагедия на дороге. В ДТП погиб человек. Страшная авария.",
        "Большой праздник! Победа нашей команды на соревнованиях!",
        "Пожар в жилом доме. К счастью, никто не пострадал."
    ]
    
    for i, text in enumerate(test_texts, 1):
        print(f"\n{i}. Текст: \"{text[:60]}...\"")
        result = analyzer.analyze(text)
        print(f"   Sentiment: {result['label']} (score: {result['score']})")
        print(f"   Emotions: {result['emotions']}")
        print(f"   Words: +{result['word_counts']['positive']} ={result['word_counts']['neutral']} -{result['word_counts']['negative']}")
    
    # Test distribution
    print("\n"+ "="*60)
    results = analyzer.analyze_batch(test_texts)
    dist = analyzer.get_sentiment_distribution(results)
    print("📊 Distribution:")
    print(f"   Positive: {dist['positive']} ({dist['positive_pct']}%)")
    print(f"   Neutral:  {dist['neutral']} ({dist['neutral_pct']}%)")
    print(f"   Negative: {dist['negative']} ({dist['negative_pct']}%)")
    
    print("\n✅ Test completed!")

