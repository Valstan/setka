#!/usr/bin/env python3
"""
Deep VK token diagnostic.
Checks each token's app ID, permissions, and what VK thinks about the token.
"""
import sys

sys.path.insert(0, "/home/valstan/SETKA")

print("=" * 70)
print("DEEP VK TOKEN DIAGNOSTIC")
print("=" * 70)

import vk_api

from config.runtime import VK_TOKENS

for name in sorted(VK_TOKENS.keys()):
    token = VK_TOKENS[name]
    print(f"\n{'='*70}")
    print(f"TOKEN: {name}")
    print(f"  Full token: {token}")
    print(f"  Length: {len(token)}")
    print(f"  Prefix: {token[:30]}")

    # Create session
    session = vk_api.VkApi(token=token)
    api = session.get_api()

    # 1. Try secure.getTokenInfo (if available)
    try:
        # Get basic info about the token's app
        info = api.users.get(fields="photo_50")
        print(f"  User: {info[0].get('first_name')} {info[0].get('last_name')}")
        print(f"  User ID: {info[0].get('id')}")
    except vk_api.exceptions.ApiError as e:
        print(f"  users.get FAILED: {e}")
        continue

    # 2. Try account.getAppPermissions
    try:
        perms = api.account.getAppPermissions()
        print(f"  Permissions (bits): {perms}")
        # Decode common permissions
        perm_bits = {
            1: "notify",
            2: "friends",
            4: "photos",
            8: "audio",
            16: "video",
            32: "docs",
            64: "notes",
            128: "pages",
            2048: "wall",
            4096: "messages",
            8192: "email",
            524288: "notify",
        }
        for bit, pname in perm_bits.items():
            if perms & bit:
                print(f"    ✓ {pname}")
    except vk_api.exceptions.ApiError as e:
        print(f"  account.getAppPermissions FAILED: {e}")

    # 3. Try apps.get (shows which app the token belongs to)
    try:
        apps = api.apps.get(count=1)
        if apps.get("items"):
            app = apps["items"][0]
            print(f"  Token's app context: {app.get('title', 'N/A')} (id={app.get('id')})")
    except vk_api.exceptions.ApiError as e:
        print(f"  apps.get FAILED: {e}")

    # 4. Try wall.get (read) vs wall.post (write)
    try:
        wall = api.wall.get(owner_id=-137760500, count=1)
        print(f"  wall.get (read): OK - {len(wall.get('items', []))} posts")
    except vk_api.exceptions.ApiError as e:
        print(f"  wall.get (read) FAILED: {e}")

    try:
        resp = api.wall.post(owner_id=-137760500, message=f"Диагностика токена {name} - авто-тест")
        post_id = resp.get("post_id")
        print(f"  wall.post (write): OK - Post #{post_id}")
        print(f"  URL: https://vk.com/wall-137760500_{post_id}")
    except vk_api.exceptions.ApiError as e:
        err_str = str(e)
        if "Application is blocked" in err_str:
            print("  wall.post BLOCKED: Приложение VK заблокировано")
        elif "Access denied" in err_str:
            print("  wall.post DENIED: Нет прав на запись в группу")
        else:
            print(f"  wall.post ERROR: {e}")

print("\n" + "=" * 70)
print("SUMMARY")
print("=" * 70)
print("\nТокены:")
for name in sorted(VK_TOKENS.keys()):
    print(f"  {name}: {VK_TOKENS[name][:20]}...{VK_TOKENS[name][-5:]}")
