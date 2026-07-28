"""Register (or update) an OIDC client of ЕСА вМалмыже.рф (ADR-0002 §8 — вручную).

Usage (на хосте setka, под env приложения):
    python scripts/register_oidc_client.py \
        --client-id trener --name "Тренер" \
        --redirect-uri "https://xn--80apfevho.xn--80adkdyec4j.xn--p1ai/auth/vk/callback" \
        --redirect-uri "http://localhost:3000/auth/vk/callback" \
        --scopes "openid profile email"

Брендинг страницы входа (миграция 072) ставится теми же флагами
``--brand-*`` — иначе ``/login`` покажет голое ``name`` вместо карточки
сервиса, а дозаписывать JSON руками в прод-БД пришлось бы отдельным SQL:

    python scripts/register_oidc_client.py \
        --client-id sabantuy --name "Сабантуй в Малмыже" \
        --redirect-uri "https://<punycode>/api/auth/esa/callback" \
        --scopes "openid profile" \
        --brand-title "Сабантуй в Малмыже" --brand-icon "🌷" \
        --brand-accent "#1f7a4d" \
        --brand-sub "Программа праздника, фотостена и народная лента"

Печатает client_secret ОДИН раз (в БД — только scrypt-hash). Повторный
запуск с тем же --client-id обновляет redirect_uris/scopes/name/брендинг;
секрет перегенерируется только с --rotate-secret.

Секрет попадает в stdout — на проде перенаправляй вывод в root-only файл,
не в общий лог и не в чат (#008).
"""

from __future__ import annotations

import argparse
import asyncio
import re
import secrets
import sys
from typing import Dict, Optional

from sqlalchemy import select

from database import models  # noqa: F401 — конфигурация мапперов
from database.connection import AsyncSessionLocal
from database.models_extended import OAuthClient
from modules.radar.auth import hash_password

# Ключи branding, которые понимает страница входа (modules/radar_id/branding.py).
BRAND_KEYS = ("title", "icon", "accent", "sub")

_ACCENT_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


def _accent(value: str) -> str:
    """argparse-тип для --brand-accent: строгий ``#rrggbb``.

    Брендинг подставляется в стиль страницы, а обработка его fail-open —
    кривой цвет не упадёт, а молча отрисуется дефолтным. Ловим на входе.
    """
    if not _ACCENT_RE.match(value):
        raise argparse.ArgumentTypeError(f"ожидался цвет вида #1f7a4d, получено {value!r}")
    return value


def merge_branding(existing: Optional[dict], given: Dict[str, str]) -> Optional[dict]:
    """Слить переданные ``--brand-*`` с тем, что уже лежит в БД.

    Ни одного флага не передали → ``None``-запрос «не трогай»: возвращаем
    ``existing`` как есть (в т.ч. ``None``). Передали часть → правим только
    её, остальные ключи карточки переживают частичное обновление.
    """
    if not given:
        return existing
    merged = dict(existing or {})
    merged.update(given)
    return merged


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register/update an ЕСА OIDC client")
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
    parser.add_argument("--brand-title", help="заголовок карточки на /login (по умолчанию name)")
    parser.add_argument("--brand-icon", help="эмодзи-значок сервиса")
    parser.add_argument("--brand-accent", type=_accent, help="акцентный цвет, #rrggbb")
    parser.add_argument("--brand-sub", help="подпись под заголовком")
    return parser.parse_args(argv)


def _given_branding(args: argparse.Namespace) -> Dict[str, str]:
    """Только те brand-ключи, что реально переданы в командной строке."""
    values = {key: getattr(args, f"brand_{key}") for key in BRAND_KEYS}
    return {key: value for key, value in values.items() if value is not None}


async def main(argv=None) -> int:
    args = _parse_args(argv)

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
        row.branding = merge_branding(row.branding, _given_branding(args))
        row.is_confidential = not args.public
        row.is_active = True
        if args.public:
            row.client_secret_hash = None
        elif need_secret:
            secret_plain = secrets.token_urlsafe(32)
            row.client_secret_hash = hash_password(secret_plain)
        branding = row.branding
        await session.commit()

    print(f"client_id: {args.client_id}")
    print(f"redirect_uris: {args.redirect_uri}")
    print(f"allowed_scopes: {args.scopes}")
    print(f"confidential: {not args.public}")
    print(f"branding: {branding or '— (страница входа покажет name)'}")
    if secret_plain:
        print("client_secret (показывается ОДИН раз, передать клиенту по защищённому каналу):")
        print(secret_plain)
    elif not args.public:
        print("client_secret: без изменений (--rotate-secret для перегенерации)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
