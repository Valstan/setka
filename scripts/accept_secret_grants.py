#!/usr/bin/env python3
"""Принять входящие выдачи (grant'ы) в комнате КАРМАНа по allowlist — шаг деплоя.

Двусторонний grant (D-061, мандат brain 2026-09-03): источник предложил — мы
принимаем только имена из ``modules.secrets_grants.GRANT_ALLOWLIST``. Остальные
остаются pending и уходят в вывод как «предложено, не принято».

Запуск на боксе под env приложения (нужен ``SECRETS_TOKEN`` — пишущий токен
комнаты ``setka``):

    sudo bash -c 'set -a; . /etc/setka/setka.env; . /etc/setka/secrets-token.env; set +a;
                  cd ~/SETKA && venv/bin/python scripts/accept_secret_grants.py'

Опции: ``--dry-run`` — показать решение без POST; ``--list`` — только перечислить
pending. Значений секретов скрипт не видит и не печатает: в ответах grants API
их нет, а после принятия ключ до процесса довозит обычный bootstrap на старте.

Коды выхода: 0 — ок (в т.ч. нуль pending), 1 — нет токена / vault недоступен,
2 — хотя бы одну разрешённую выдачу принять не удалось.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modules import secrets_grants as sg  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="решить, но не принимать")
    parser.add_argument("--list", action="store_true", help="только перечислить pending")
    args = parser.parse_args(argv)

    token = (os.getenv("SECRETS_TOKEN") or "").strip()
    if not token:
        print(
            "SECRETS_TOKEN не задан — токен комнаты setka нужен для приёма выдач", file=sys.stderr
        )
        return 1
    vault_url = (os.getenv("SECRETS_VAULT_URL") or "").strip() or None

    try:
        if args.list:
            for g in sg.list_pending(token, vault_url=vault_url):
                name = g.get("aliasKey") or g.get("alias") or g.get("key")
                src = g.get("sourceSlug") or g.get("source_slug") or "?"
                print(f"pending id={g.get('id')} {name} от {src} → {sg.decide(g)}")
            return 0
        summary = sg.accept_pending(token, vault_url=vault_url, dry_run=args.dry_run)
    except Exception as e:  # noqa: BLE001 — одна строка причины вместо трейса
        print(f"vault недоступен или ответ не по контракту: {e}", file=sys.stderr)
        return 1

    print(sg.format_summary(summary))
    return 2 if summary.get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
