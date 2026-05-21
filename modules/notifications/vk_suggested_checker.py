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

    # VK error codes where a community-token typically fails on wall.get(filter=suggests)
    # because such token doesn't carry "manage" scope. Fall back to the user-token.
    _COMMUNITY_FALLBACK_CODES = {15, 27}

    def _api_for(self, group_id: int):
        """Вернуть (vk_api_handle, via_community) для группы."""
        cid = abs(int(group_id))
        tok = self.community_tokens.get(cid)
        if tok:
            return vk_api.VkApi(token=tok).get_api(), True
        return self.vk, False

    def _wall_get_suggests(self, api, group_id: int):
        """One VK API call; isolated for easier mocking and fallback logic."""
        return api.wall.get(owner_id=group_id, filter='suggests', count=100)

    def check_suggested_posts(self, group_id: int) -> Dict[str, Any]:
        """Проверить предложенные посты в группе.

        Если первый вызов идёт через community-token и VK возвращает code 15/27
        (нет прав на `wall.get(filter=suggests)` — community-токены обычно не
        имеют scope `manage`), повторяем через user-token.
        """
        positive_id = abs(group_id)
        api, via_community = self._api_for(group_id)
        try:
            result = self._wall_get_suggests(api, group_id)
            via = "community-token" if via_community else "user-token"
        except ApiError as e:
            if via_community and e.code in self._COMMUNITY_FALLBACK_CODES:
                logger.info(
                    "Group %s: community-token failed on suggests with code %s, retrying via user-token",
                    group_id, e.code,
                )
                try:
                    result = self._wall_get_suggests(self.vk, group_id)
                    via = "community-fallback-user"
                except ApiError as e2:
                    return self._format_error(group_id, e2)
                except Exception as e2:
                    logger.error(f"Error retrying suggests for group {group_id}: {e2}")
                    return self._empty_result(group_id, str(e2))
            else:
                return self._format_error(group_id, e)
        except Exception as e:
            logger.error(f"Error checking group {group_id}: {e}")
            return self._empty_result(group_id, str(e))

        count = result.get('count', 0)
        logger.info(f"Group {group_id}: {count} suggested posts (via {via})")
        return {
            'has_suggested': count > 0,
            'count': count,
            'group_id': group_id,
            'url': f"https://vk.com/club{positive_id}",
            'via': via,
        }

    def _format_error(self, group_id: int, err: ApiError) -> Dict[str, Any]:
        if err.code == 15:
            logger.warning(f"No access to suggested posts for group {group_id}")
        elif err.code == 5:
            logger.error(f"Token invalid for group {group_id}")
        else:
            logger.error(f"VK API error for group {group_id}: {err}")
        return self._empty_result(group_id, str(err))

    @staticmethod
    def _empty_result(group_id: int, error: str) -> Dict[str, Any]:
        return {
            'has_suggested': False,
            'count': 0,
            'group_id': group_id,
            'error': error,
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

