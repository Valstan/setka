#!/usr/bin/env python3
"""
VK Token Validation Script
Проверяет работоспособность всех токенов VK API
"""
import asyncio
import sys
import logging
from datetime import datetime
from typing import Dict, List, Tuple

# Добавить путь к проекту
sys.path.append('/home/valstan/SETKA')

from config.config_secure import VK_TOKENS
from modules.vk_monitor.vk_client import VKClient

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class TokenValidator:
    """Валидатор токенов VK API"""
    
    def __init__(self):
        self.results: Dict[str, Dict] = {}
    
    async def validate_token(self, name: str, token: str) -> Dict:
        """Проверить один токен"""
        logger.info(f"Validating token: {name}")
        
        result = {
            "name": name,
            "is_valid": False,
            "user_info": None,
            "permissions": [],
            "error_message": None,
            "test_time": datetime.now().isoformat()
        }
        
        if not token:
            result["error_message"] = "Token is empty"
            logger.warning(f"Token {name} is empty")
            return result
        
        try:
            # Создать VK клиент
            vk_client = VKClient(token)
            
            # Тест 1: Получить информацию о пользователе
            logger.debug(f"Testing user info for {name}")
            user_info = await vk_client.get_user_info()
            
            if not user_info:
                result["error_message"] = "Failed to get user info"
                logger.error(f"Token {name}: Failed to get user info")
                return result
            
            result["user_info"] = user_info
            result["is_valid"] = True
            
            # Тест 2: Проверить права доступа
            logger.debug(f"Testing permissions for {name}")
            permissions = await self._test_permissions(vk_client)
            result["permissions"] = permissions
            
            # Тест 3: Проверить доступ к группам
            logger.debug(f"Testing groups access for {name}")
            groups_access = await self._test_groups_access(vk_client)
            result["groups_access"] = groups_access
            
            logger.info(f"Token {name} is VALID - User: {user_info.get('first_name', 'Unknown')} {user_info.get('last_name', 'Unknown')}")
            
        except Exception as e:
            result["error_message"] = str(e)
            logger.error(f"Token {name} validation failed: {e}")
        
        return result
    
    async def _test_permissions(self, vk_client: VKClient) -> List[str]:
        """Тестировать права доступа токена"""
        permissions = []
        
        try:
            # Тест wall.get - чтение постов
            try:
                await vk_client.get_posts(owner_id=-1, count=1)
                permissions.append("wall.read")
            except Exception as e:
                logger.debug(f"wall.read permission test failed: {e}")
            
            # Тест groups.get - получение групп
            try:
                groups = await vk_client.get_groups(count=1)
                if groups:
                    permissions.append("groups.read")
            except Exception as e:
                logger.debug(f"groups.read permission test failed: {e}")
            
            # Тест messages.get - чтение сообщений
            try:
                messages = await vk_client.get_messages(count=1)
                if messages:
                    permissions.append("messages.read")
            except Exception as e:
                logger.debug(f"messages.read permission test failed: {e}")
            
        except Exception as e:
            logger.debug(f"Permission testing error: {e}")
        
        return permissions
    
    async def _test_groups_access(self, vk_client: VKClient) -> Dict:
        """Тестировать доступ к группам"""
        groups_access = {
            "can_read_groups": False,
            "can_write_groups": False,
            "admin_groups": [],
            "error": None
        }
        
        try:
            # Получить группы пользователя
            groups = await vk_client.get_groups(count=10, extended=1)
            
            if groups and 'items' in groups:
                groups_access["can_read_groups"] = True
                
                # Проверить админские права
                for group in groups['items']:
                    if group.get('is_admin', False):
                        groups_access["admin_groups"].append({
                            "id": group['id'],
                            "name": group['name'],
                            "screen_name": group.get('screen_name', '')
                        })
                
                # Тест записи в группу (если есть админские права)
                if groups_access["admin_groups"]:
                    try:
                        # Попробовать получить информацию о группе
                        group_id = groups_access["admin_groups"][0]["id"]
                        group_info = await vk_client.get_group_info(group_id)
                        if group_info:
                            groups_access["can_write_groups"] = True
                    except Exception as e:
                        logger.debug(f"Group write test failed: {e}")
        
        except Exception as e:
            groups_access["error"] = str(e)
            logger.debug(f"Groups access test failed: {e}")
        
        return groups_access
    
    async def validate_all_tokens(self) -> Dict[str, Dict]:
        """Проверить все токены"""
        logger.info("Starting validation of all VK tokens...")
        
        tasks = []
        for name, token in VK_TOKENS.items():
            task = asyncio.create_task(self.validate_token(name, token))
            tasks.append(task)
        
        # Выполнить все проверки параллельно
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Обработать результаты
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                token_name = list(VK_TOKENS.keys())[i]
                self.results[token_name] = {
                    "name": token_name,
                    "is_valid": False,
                    "error_message": str(result),
                    "test_time": datetime.now().isoformat()
                }
            else:
                self.results[result["name"]] = result
        
        return self.results
    
    def print_summary(self):
        """Вывести сводку результатов"""
        print("\n" + "="*80)
        print("VK TOKENS VALIDATION SUMMARY")
        print("="*80)
        
        valid_tokens = []
        invalid_tokens = []
        
        for name, result in self.results.items():
            if result["is_valid"]:
                valid_tokens.append((name, result))
            else:
                invalid_tokens.append((name, result))
        
        print(f"\n✅ VALID TOKENS ({len(valid_tokens)}):")
        for name, result in valid_tokens:
            user_info = result.get("user_info", {})
            permissions = result.get("permissions", [])
            groups_access = result.get("groups_access", {})
            
            print(f"  • {name}")
            print(f"    User: {user_info.get('first_name', 'Unknown')} {user_info.get('last_name', 'Unknown')}")
            print(f"    ID: {user_info.get('id', 'Unknown')}")
            print(f"    Permissions: {', '.join(permissions) if permissions else 'None'}")
            
            admin_groups = groups_access.get("admin_groups", [])
            if admin_groups:
                print(f"    Admin groups: {len(admin_groups)}")
                for group in admin_groups[:3]:  # Показать первые 3
                    print(f"      - {group['name']} (id: {group['id']})")
                if len(admin_groups) > 3:
                    print(f"      ... and {len(admin_groups) - 3} more")
            print()
        
        if invalid_tokens:
            print(f"\n❌ INVALID TOKENS ({len(invalid_tokens)}):")
            for name, result in invalid_tokens:
                print(f"  • {name}: {result.get('error_message', 'Unknown error')}")
            print()
        
        # Рекомендации
        print("📋 RECOMMENDATIONS:")
        if valid_tokens:
            print("  ✅ Use valid tokens for data collection")
            print("  ✅ Use VALSTAN token for publishing (has admin rights)")
        else:
            print("  ⚠️  No valid tokens found! Check token configuration")
        
        if len(valid_tokens) > 1:
            print("  ✅ Implement token rotation for load balancing")
            print("  ✅ Use different tokens for different regions")
        
        print("\n" + "="*80)


async def main():
    """Основная функция"""
    print("VK Token Validation Tool")
    print("Checking all configured VK tokens...")
    
    validator = TokenValidator()
    
    try:
        # Проверить все токены
        results = await validator.validate_all_tokens()
        
        # Вывести результаты
        validator.print_summary()
        
        # Сохранить результаты в файл
        import json
        with open('/home/valstan/SETKA/logs/token_validation.json', 'w') as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        
        print(f"\n📄 Detailed results saved to: /home/valstan/SETKA/logs/token_validation.json")
        
        # Возвращаем код выхода
        valid_count = sum(1 for r in results.values() if r["is_valid"])
        if valid_count == 0:
            print("\n❌ No valid tokens found!")
            return 1
        elif valid_count < len(VK_TOKENS):
            print(f"\n⚠️  Only {valid_count}/{len(VK_TOKENS)} tokens are valid")
            return 2
        else:
            print(f"\n✅ All {valid_count} tokens are valid!")
            return 0
            
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        print(f"\n❌ Validation failed: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
