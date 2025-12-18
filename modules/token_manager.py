"""
VK Token Manager - Manages VK tokens hierarchy and validation
"""
import requests
import logging
from typing import Dict, List, Tuple, Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class VKTokenManager:
    """Manages VK tokens with hierarchy (MAIN/AUXILIARY)"""
    
    def __init__(self):
        self.main_tokens = {}
        self.auxiliary_tokens = {}
        self.load_tokens()
    
    def load_tokens(self):
        """Load tokens from config"""
        try:
            from config.config_secure import VK_MAIN_TOKENS, VK_AUXILIARY_TOKENS
            self.main_tokens = VK_MAIN_TOKENS.copy()
            self.auxiliary_tokens = VK_AUXILIARY_TOKENS.copy()
            logger.info(f"Loaded {len(self.main_tokens)} main tokens and {len(self.auxiliary_tokens)} auxiliary tokens")
        except ImportError as e:
            logger.error(f"Failed to load tokens: {e}")
    
    def validate_token(self, token: str) -> Tuple[bool, str, Dict]:
        """
        Validate VK token and get user info
        
        Returns:
            (is_valid, message, user_info)
        """
        if not token:
            return False, "Empty token", {}
        
        try:
            # Get user info
            url = 'https://api.vk.com/method/users.get'
            params = {
                'access_token': token,
                'v': '5.131'
            }
            response = requests.get(url, params=params, timeout=10)
            data = response.json()
            
            if 'error' in data:
                return False, f"Error: {data['error']['error_msg']}", {}
            
            user = data['response'][0]
            user_info = {
                'user_id': user['id'],
                'name': f"{user['first_name']} {user['last_name']}",
                'first_name': user['first_name'],
                'last_name': user['last_name']
            }
            
            # Check admin groups
            groups_url = 'https://api.vk.com/method/groups.get'
            groups_params = {
                'access_token': token,
                'extended': 1,
                'filter': 'admin',
                'v': '5.131'
            }
            
            groups_response = requests.get(groups_url, params=groups_params, timeout=10)
            groups_data = groups_response.json()
            
            admin_groups = []
            if 'response' in groups_data and groups_data['response']['count'] > 0:
                admin_groups = groups_data['response']['items']
            
            user_info['admin_groups'] = admin_groups
            user_info['admin_groups_count'] = len(admin_groups)
            
            return True, "Token is valid", user_info
            
        except Exception as e:
            return False, f"Exception: {str(e)}", {}
    
    def add_token(self, name: str, token: str, token_type: str = "MAIN") -> Tuple[bool, str]:
        """
        Add new token to the system
        
        Args:
            name: Token name
            token: VK access token
            token_type: "MAIN" or "AUXILIARY"
        
        Returns:
            (success, message)
        """
        # Validate token first
        is_valid, message, user_info = self.validate_token(token)
        
        if not is_valid:
            return False, f"Token validation failed: {message}"
        
        # Check if token already exists
        if name in self.main_tokens or name in self.auxiliary_tokens:
            return False, f"Token '{name}' already exists"
        
        # Add token info
        token_info = {
            'token': token,
            'user_id': user_info['user_id'],
            'name': user_info['name'],
            'admin_groups': user_info['admin_groups_count'],
            'status': 'active',
            'added_at': datetime.now().isoformat()
        }
        
        if token_type == "MAIN":
            self.main_tokens[name] = token_info
        else:
            self.auxiliary_tokens[name] = token_info
        
        logger.info(f"Added {token_type} token '{name}' for user {user_info['name']}")
        return True, f"Token '{name}' added successfully"
    
    def remove_token(self, name: str) -> Tuple[bool, str]:
        """Remove token from system"""
        if name in self.main_tokens:
            del self.main_tokens[name]
            logger.info(f"Removed main token '{name}'")
            return True, f"Main token '{name}' removed"
        elif name in self.auxiliary_tokens:
            del self.auxiliary_tokens[name]
            logger.info(f"Removed auxiliary token '{name}'")
            return True, f"Auxiliary token '{name}' removed"
        else:
            return False, f"Token '{name}' not found"
    
    def get_token(self, name: str) -> Optional[str]:
        """Get token by name"""
        if name in self.main_tokens:
            return self.main_tokens[name]['token']
        elif name in self.auxiliary_tokens:
            return self.auxiliary_tokens[name]['token']
        return None
    
    def get_main_tokens(self) -> List[str]:
        """Get list of main token names"""
        return list(self.main_tokens.keys())
    
    def get_auxiliary_tokens(self) -> List[str]:
        """Get list of auxiliary token names"""
        return list(self.auxiliary_tokens.keys())
    
    def get_all_tokens(self) -> List[str]:
        """Get all token names"""
        return self.get_main_tokens() + self.get_auxiliary_tokens()
    
    def get_tokens_for_operation(self, operation: str) -> List[str]:
        """
        Get tokens suitable for specific operation
        
        Args:
            operation: "read", "post", "admin"
        """
        if operation == "read":
            return self.get_all_tokens()
        elif operation in ["post", "admin"]:
            return self.get_main_tokens()
        else:
            return []
    
    def get_token_info(self, name: str) -> Optional[Dict]:
        """Get detailed token information"""
        if name in self.main_tokens:
            return self.main_tokens[name]
        elif name in self.auxiliary_tokens:
            return self.auxiliary_tokens[name]
        return None
    
    def get_all_tokens_info(self) -> Dict:
        """Get information about all tokens"""
        return {
            'main_tokens': self.main_tokens,
            'auxiliary_tokens': self.auxiliary_tokens,
            'total_main': len(self.main_tokens),
            'total_auxiliary': len(self.auxiliary_tokens),
            'total': len(self.main_tokens) + len(self.auxiliary_tokens)
        }
    
    def check_all_tokens(self) -> Dict:
        """Check status of all tokens"""
        results = {
            'main_tokens': {},
            'auxiliary_tokens': {},
            'summary': {
                'total_checked': 0,
                'working': 0,
                'broken': 0
            }
        }
        
        # Check main tokens
        for name, info in self.main_tokens.items():
            is_valid, message, _ = self.validate_token(info['token'])
            results['main_tokens'][name] = {
                'status': 'working' if is_valid else 'broken',
                'message': message,
                'user_info': info
            }
            results['summary']['total_checked'] += 1
            if is_valid:
                results['summary']['working'] += 1
            else:
                results['summary']['broken'] += 1
        
        # Check auxiliary tokens
        for name, info in self.auxiliary_tokens.items():
            is_valid, message, _ = self.validate_token(info['token'])
            results['auxiliary_tokens'][name] = {
                'status': 'working' if is_valid else 'broken',
                'message': message,
                'user_info': info
            }
            results['summary']['total_checked'] += 1
            if is_valid:
                results['summary']['working'] += 1
            else:
                results['summary']['broken'] += 1
        
        return results


# Global instance
token_manager = VKTokenManager()
