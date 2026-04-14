#!/usr/bin/env python3
"""Quick test of new VALSTAN token"""
import vk_api

NEW_TOKEN = "vk1.a.zhWLKNwC1_wm_XRs175sIyCajxGj11IS26Zfe4w33T8RkvsEydmj4hnRlysfn21OHREwT8_AZOq9D_nD6bi8TmolioLKs_o6qmP0Dpnrk4ACfys4RsZ2jZf2pWGRQcdFe79Di8DggHGVJrn9JuE4s_I98jy3cCgpAd1e0V85VAiWoweuYe8O0E7kRlFts97k7rk07OpPw3_aZ8zTq9fUbg"

session = vk_api.VkApi(token=NEW_TOKEN)
api = session.get_api()

print("=== Testing new VALSTAN token ===")

# 1. users.get
try:
    user = api.users.get()
    print(f"  User: {user[0].get('first_name')} {user[0].get('last_name')} (id={user[0].get('id')})")
except Exception as e:
    print(f"  users.get FAILED: {e}")
    exit(1)

# 2. apps.get
try:
    apps = api.apps.get(count=1)
    if apps.get('items'):
        app = apps['items'][0]
        print(f"  App: {app.get('title')} (id={app.get('id')})")
except Exception as e:
    print(f"  apps.get FAILED: {e}")

# 3. wall.post
try:
    resp = api.wall.post(
        owner_id=-137760500,
        message="Тест нового токена VALSTAN - проверка работоспособности"
    )
    post_id = resp.get('post_id')
    print(f"  wall.post: SUCCESS! Post #{post_id}")
    print(f"  URL: https://vk.com/wall-137760500_{post_id}")
except Exception as e:
    print(f"  wall.post FAILED: {e}")
    exit(1)

print("\n✅ Token works!")
