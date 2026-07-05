"""Register (or update) an OIDC client of Радар-ID (ADR-0002 §8 — вручную).

Usage (на хосте setka, под env приложения):
    python scripts/register_oidc_client.py \
        --client-id trener --name "Тренер" \
        --redirect-uri "https://xn--80apfevho.xn--80adkdyec4j.xn--p1ai/auth/vk/callback" \
        --redirect-uri "http://localhost:3000/auth/vk/callback" \
        --scopes "openid profile email"

Печатает client_secret ОДИН раз (в БД — только scrypt-hash). Повторный
запуск с тем же --client-id обновляет redirect_uris/scopes/name; секрет
перегенерируется только с --rotate-secret.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys

from sqlalchemy import select

from database import models  # noqa: F401 — конфигурация мапперов
from database.connection import AsyncSessionLocal
from database.models_extended import OAuthClient
from modules.radar.auth import hash_password


async def main() -> int:
    parser = argparse.ArgumentParser(description="Register/update a Radar-ID OIDC client")
    parser.add_argument("--client-id", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--redirect-uri",
        action="append",
        required=True,
        help="точный redirect_uri (повторяемый флаг); punycode для .рф — G108",
    )
    parser.add_argument("--scopes", default="openid profile email")
    parser.add_argument(
        "--public", action="store_true", help="public PKCE-only клиент (без secret)"
    )
    parser.add_argument("--rotate-secret", action="store_true")
    args = parser.parse_args()

    secret_plain = None
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(OAuthClient).where(OAuthClient.client_id == args.client_id)
            )
        ).scalar_one_or_none()
        if row is None:
            row = OAuthClient(client_id=args.client_id)
            session.add(row)
            need_secret = not args.public
        else:
            need_secret = args.rotate_secret and not args.public

        row.name = args.name
        row.redirect_uris = list(args.redirect_uri)
        row.allowed_scopes = args.scopes
        row.is_confidential = not args.public
        row.is_active = True
        if args.public:
            row.client_secret_hash = None
        elif need_secret:
            secret_plain = secrets.token_urlsafe(32)
            row.client_secret_hash = hash_password(secret_plain)
        await session.commit()

    print(f"client_id: {args.client_id}")
    print(f"redirect_uris: {args.redirect_uri}")
    print(f"allowed_scopes: {args.scopes}")
    print(f"confidential: {not args.public}")
    if secret_plain:
        print("client_secret (показывается ОДИН раз, передать клиенту по защищённому каналу):")
        print(secret_plain)
    elif not args.public:
        print("client_secret: без изменений (--rotate-secret для перегенерации)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
