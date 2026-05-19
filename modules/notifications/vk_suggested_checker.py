"""
VK Suggested Posts Checker

Проверка предложенных постов в главных группах регионов VK.

VK API:
- wall.get с filter='suggests' возвращает предложенные записи
- Требуется токен с правами на управление группой

Поддерживаем 2 источника токенов:
1. community access token для каждой группы (приоритет). Снимает нагрузку
   с пользовательского токена и не упирается в права на чужой стене.
2. user-токен (fallback) — раньше единственный путь.
"""
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
import vk_api
from vk_api.exceptions import ApiError

logger = logging.getLogger(__name__)


class VKSuggestedChecker:
    """Проверка предложенных постов в VK группах."""

    def __init__(self, vk_token: str, community_tokens: Optional[Dict[int, str]] = None):
        try:
            self.session = vk_api.VkApi(token=vk_token)
            self.vk = self.session.get_api()
            self.community_tokens = dict(community_tokens or {})
            logger.info(
                "VK Suggested Checker initialized (community tokens: %d)",
                len(self.community_tokens),
            )
        except Exception as e:
            logger.error(f"Failed to initialize VK Suggested Checker: {e}")
            raise

    def _api_for(self, group_id: int):
        """Вернуть (vk_api_handle, via_community) для группы."""
        cid = abs(int(group_id))
        tok = self.community_tokens.get(cid)
        if tok:
            return vk_api.VkApi(token=tok).get_api(), True
        return self.vk, False

    def check_suggested_posts(self, group_id: int) -> Dict[str, Any]:
        """Проверить предложенные посты в группе."""
        try:
            positive_id = abs(group_id)
            api, _via_community = self._api_for(group_id)
            # wall.get требует owner_id всегда (это не «свой контекст», как у messages.getConversations).
            result = api.wall.get(
                owner_id=group_id,
                filter='suggests',
                count=100,
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

