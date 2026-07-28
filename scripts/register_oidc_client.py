"""Register (or update) an OIDC client of ЕСА вМалмыже.рф — операторский путь.

С 2026-07-28 (ADR-0010) основной способ подключения — **self-serve по HTTP**:
проект-клиент сам зовёт ``POST /api/ecosystem/oidc-clients`` с экосистемным
ключом. Этот скрипт остался для случаев, когда self-serve не годится:

* правка чужого клиента (у оператора нет его ``client_secret``);
* аварийная ротация секрета за клиента;
* регистрация до того, как у проекта появился экосистемный ключ.

Валидация и запись — общие с HTTP (:mod:`modules.ecosystem.provisioning`),
чтобы два пути не разъехались правилами. Разница ровно одна: скрипт зовёт ядро
с ``allow_update=True`` (root на хосте и так может всё).

Usage (на хосте setka, под env приложения):
    python scripts/register_oidc_client.py \
        --client-id trener --name "Тренер" \
        --redirect-uri "https://xn--80apfevho.xn--80adkdyec4j.xn--p1ai/auth/vk/callback" \
        --redirect-uri "http://localhost:3000/auth/vk/callback" \
        --scopes "openid profile email"

Брендинг страницы входа (миграция 072) ставится флагами ``--brand-*`` — иначе
``/login`` покажет голое ``name`` вместо карточки сервиса:

    python scripts/register_oidc_client.py \
        --client-id sabantuy --name "Сабантуй в Малмыже" \
        --redirect-uri "https://<punycode>/api/auth/esa/callback" \
        --scopes "openid profile" \
        --brand-title "Сабантуй в Малмыже" --brand-icon "🌷" \
        --brand-accent "#1f7a4d" \
        --brand-sub "Программа праздника, фотостена и народная лента"

Печатает client_secret ОДИН раз (в БД — только scrypt-hash). Повторный запуск
с тем же --client-id обновляет redirect_uris/scopes/name/брендинг; секрет
перегенерируется только с --rotate-secret.

Секрет попадает в stdout — на проде перенаправляй вывод в root-only файл,
не в общий лог и не в чат (#008).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from typing import Dict, Optional

from modules.ecosystem.provisioning import (
    ACCENT_RE,
    BRAND_KEYS,
    ProvisioningError,
    merge_branding,
    provision_oidc_client,
)

__all__ = ["merge_branding", "BRAND_KEYS", "main"]


def _accent(value: str) -> str:
    """argparse-тип для --brand-accent: строгий ``#rrggbb``.

    Брендинг подставляется в стиль страницы, а обработка его fail-open —
    кривой цвет не упадёт, а молча отрисуется дефолтным. Ловим на входе.
    """
    if not ACCENT_RE.match(value):
        raise argparse.ArgumentTypeError(f"ожидался цвет вида #1f7a4d, получено {value!r}")
    return value


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

    try:
        result = await provision_oidc_client(
            client_id=args.client_id,
            name=args.name,
            redirect_uris=list(args.redirect_uri),
            scopes=args.scopes,
            public=args.public,
            branding=_given_branding(args),
            rotate_secret=args.rotate_secret,
            allow_update=True,
        )
    except ProvisioningError as e:
        print(f"отказ ({e.code}): {e.message}", file=sys.stderr)
        return 2

    secret_plain: Optional[str] = result.secret
    print(f"client_id: {result.identifier}")
    print(f"action: {result.action}")
    print(f"redirect_uris: {result.details.get('redirect_uris')}")
    print(f"allowed_scopes: {result.details.get('allowed_scopes')}")
    print(f"confidential: {result.details.get('confidential')}")
    print(f"branding: {result.details.get('branding') or '— (страница входа покажет name)'}")
    if secret_plain:
        print("client_secret (показывается ОДИН раз, передать клиенту по защищённому каналу):")
        print(secret_plain)
    elif not args.public:
        print("client_secret: без изменений (--rotate-secret для перегенерации)")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
