#!/usr/bin/env python3
"""Check VK tokens on VPS"""
import os, sys
sys.path.insert(0, '/home/valstan/SETKA')

print("=== VK TOKENS from env ===")
for k, v in sorted(os.environ.items()):
    if k.startswith("VK_TOKEN"):
        name = k.replace("VK_TOKEN_", "")
        token_preview = v[:15] + "..." + v[-5:] if len(v) > 20 else v
        print(f"  {name}: {token_preview} (len={len(v)})")

print()
print("=== VK_PUBLISH_TOKEN_NAME ===")
print(f"  {os.environ.get('VK_PUBLISH_TOKEN_NAME', 'NOT SET')}")

print()
from config.runtime import VK_TOKENS, get_publish_token, VK_PUBLISH_TOKEN_NAME
print(f"  VK_TOKENS keys: {list(VK_TOKENS.keys())}")
print(f"  VK_PUBLISH_TOKEN_NAME (from config): {VK_PUBLISH_TOKEN_NAME}")

pt = get_publish_token()
if pt:
    print(f"  Publish token preview: {pt[:15]}...{pt[-5:]}")
    for n, t in VK_TOKENS.items():
        if t == pt:
            print(f"  => Name: {n}")
            break
else:
    print("  ERROR: No publish token found!")

# Test BOTH tokens
print()
print("=== Testing BOTH tokens ===")
from modules.vk_monitor.vk_client import VKClient
import vk_api

for token_name in ['VITA', 'VALSTAN']:
    token = VK_TOKENS.get(token_name)
    if not token:
        print(f"\n  {token_name}: NOT FOUND")
        continue
    
    print(f"\n--- {token_name} ---")
    client = VKClient(token)
    try:
        # Check app info
        apps = client.vk.apps.get()
        app_info = apps.get('items', [{}])[0] if apps.get('items') else {}
        print(f"  App: {app_info.get('title', 'N/A')} (id={app_info.get('id', 'N/A')})")
    except vk_api.exceptions.ApiError as e:
        print(f"  apps.get ERROR: {e}")
    
    try:
        user = client.vk.users.get()
        if user:
            print(f"  User: {user[0].get('first_name', '')} {user[0].get('last_name', '')}")
    except vk_api.exceptions.ApiError as e:
        print(f"  users.get ERROR: {e}")
    
    # Try wall.post
    try:
        resp = client.vk.wall.post(
            owner_id=-137760500,
            message=f"Тест токена {token_name} - проверка"
        )
        post_id = resp.get('post_id')
        print(f"  wall.post SUCCESS! Post ID: {post_id}")
        print(f"  URL: https://vk.com/wall-137760500_{post_id}")
    except vk_api.exceptions.ApiError as e:
        err_str = str(e)
        if 'Application is blocked' in err_str:
            print(f"  wall.post BLOCKED: Приложение VK заблокировано")
        else:
            print(f"  wall.post ERROR: {e}")
