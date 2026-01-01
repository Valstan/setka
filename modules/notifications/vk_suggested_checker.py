"""
VK Suggested Posts Checker

Проверка предложенных постов в главных группах регионов VK.

VK API:
- wall.get с filter='suggests' возвращает предложенные записи
- Требуется токен с правами на управление группой
"""
import logging
from typing import List, Dict, Any
from datetime import datetime
import vk_api
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


class VKSuggestedChecker:
    """Проверка предложенных постов в VK группах"""
    
    def __init__(self, vk_token: str):
        """
        Инициализация checker
        
        Args:
            vk_token: VK access token с правами на управление группами
        """
        try:
            self.session = vk_api.VkApi(token=vk_token)
            self.vk = self.session.get_api()
            logger.info("VK Suggested Checker initialized")
        except Exception as e:
            logger.error(f"Failed to initialize VK Suggested Checker: {e}")
            raise
    
    def check_suggested_posts(self, group_id: int) -> Dict[str, Any]:
        """
        Проверить предложенные посты в группе
        
        Args:
            group_id: ID группы VK (отрицательное число)
            
        Returns:
            Dict с информацией:
                - has_suggested: bool - есть ли предложенные посты
                - count: int - количество предложенных постов
                - group_id: int - ID группы
                - url: str - ссылка на предложку
        """
        try:
            # Убираем минус для запроса
            positive_id = abs(group_id)
            
            # Получаем предложенные записи
            result = self.vk.wall.get(
                owner_id=group_id,
                filter='suggests',
                count=100  # Максимум для проверки
            )
            
            count = result.get('count', 0)
            
            logger.info(f"Group {group_id}: {count} suggested posts")
            
            # Простая ссылка на группу (предложенные посты видны в разделе "Предложенные записи")
            return {
                'has_suggested': count > 0,
                'count': count,
                'group_id': group_id,
                'url': f"https://vk.com/club{positive_id}"
            }
            
        except ApiError as e:
            # Если нет прав или группа недоступна
            if e.code == 15:  # Access denied
                logger.warning(f"No access to suggested posts for group {group_id}")
            elif e.code == 5:  # Authorization failed
                logger.error(f"Token invalid for group {group_id}")
            else:
                logger.error(f"VK API error for group {group_id}: {e}")
            
            return {
                'has_suggested': False,
                'count': 0,
                'group_id': group_id,
                'error': str(e)
            }
            
        except Exception as e:
            logger.error(f"Error checking group {group_id}: {e}")
            return {
                'has_suggested': False,
                'count': 0,
                'group_id': group_id,
                'error': str(e)
            }
    
    async def check_all_region_groups(self, region_groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Проверить предложенные посты во всех главных группах регионов
        
        Args:
            region_groups: Список dict с полями:
                - region_id: int
                - region_name: str
                - region_code: str
                - vk_group_id: int
        
        Returns:
            Список уведомлений о группах с предложенными постами
        """
        notifications = []
        
        for group_info in region_groups:
            if not group_info.get('vk_group_id'):
                continue
            
            result = self.check_suggested_posts(group_info['vk_group_id'])
            
            if result['has_suggested']:
                notification = {
                    'region_id': group_info['region_id'],
                    'region_name': group_info['region_name'],
                    'region_code': group_info['region_code'],
                    'vk_group_id': result['group_id'],
                    'suggested_count': result['count'],
                    'url': result['url'],
                    'checked_at': datetime.now().isoformat()
                }
                notifications.append(notification)
                
                logger.info(f"📬 {group_info['region_name']}: {result['count']} suggested posts")
        
        logger.info(f"Found {len(notifications)} groups with suggested posts")
        
        return notifications


if __name__ == "__main__":
    # Простой тест
    import asyncio
    import sys
    import os
    from datetime import datetime
    
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from config.runtime import VK_TOKENS
    
    async def test():
        vk_token = VK_TOKENS.get("VALSTAN")
        if not vk_token:
            print("❌ VK token not found")
            return
        
        checker = VKSuggestedChecker(vk_token)
        
        # Тест на одной группе (Малмыж Инфо)
        result = checker.check_suggested_posts(-158787639)
        print(f"Result: {result}")
    
    asyncio.run(test())

