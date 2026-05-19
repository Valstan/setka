#!/usr/bin/env python3
"""
Quick smoke-test of the configured VALSTAN VK token.

Reads VK_TOKEN_VALSTAN from /etc/setka/setka.env via config.runtime.
Performs users.get, apps.get, and a wall.post into VK_TEST_GROUP_ID
(or the explicit --owner-id argument).

Usage:
    python scripts/test_new_valstan_token.py [--owner-id -137760500] [--token-name VALSTAN]
"""
import argparse
import sys

sys.path.insert(0, "/home/valstan/SETKA")

import vk_api  # noqa: E402

from config.runtime import VK_TOKENS, VK_TEST_GROUP_ID  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke-test a VK token from env")
    parser.add_argument(
        "--token-name",
        default="VALSTAN",
        help="Key in VK_TOKENS (default: VALSTAN)",
    )
    parser.add_argument(
        "--owner-id",
        type=int,
        default=VK_TEST_GROUP_ID,
        help="VK group owner_id to post into (negative for groups). "
        "Default: VK_TEST_GROUP_ID from env.",
    )
    args = parser.parse_args()

    token = VK_TOKENS.get(args.token_name)
    if not token:
        print(
            f"ERROR: no token '{args.token_name}' in VK_TOKENS. "
            f"Available: {sorted(VK_TOKENS.keys())}"
        )
        return 1
    if not args.owner_id:
        print(
            "ERROR: owner_id is 0; set VK_TEST_GROUP_ID in env "
            "or pass --owner-id explicitly."
        )
        return 1

    print(f"=== Testing token '{args.token_name}' against owner_id={args.owner_id} ===")

    session = vk_api.VkApi(token=token)
    api = session.get_api()

    try:
        user = api.users.get()
        print(f"  users.get OK: {user[0].get('first_name')} {user[0].get('last_name')} (id={user[0].get('id')})")
    except Exception as exc:
        print(f"  users.get FAILED: {exc}")
        return 1

    try:
        apps = api.apps.get(count=1)
        if apps.get("items"):
            app = apps["items"][0]
            print(f"  apps.get OK: {app.get('title')} (id={app.get('id')})")
    except Exception as exc:
        print(f"  apps.get FAILED (non-fatal): {exc}")

    try:
        resp = api.wall.post(
            owner_id=args.owner_id,
            message=f"SETKA: smoke-test of {args.token_name} token",
        )
        post_id = resp.get("post_id")
        print(f"  wall.post OK: post #{post_id}")
        print(f"  URL: https://vk.com/wall{args.owner_id}_{post_id}")
    except Exception as exc:
        print(f"  wall.post FAILED: {exc}")
        return 1

    print("\nToken works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
