"""Generate the Радар-ID RS256 signing keypair (one-off, run on prod host).

Usage (на хосте setka):
    python scripts/generate_radar_id_key.py [--out /etc/setka/radar_id_rs256.pem]

Пишет приватный PEM (chmod 0600) и печатает kid + публичный JWKS для
проверки. Ключ — критичный секрет (#008): в git не попадает, зеркало в
Карман — когда KARMAN экспонирует mirror-API (ADR-0006 brain).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from authlib.jose import JsonWebKey
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

DEFAULT_OUT = "/etc/setka/radar_id_rs256.pem"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate Radar-ID RS256 signing key")
    parser.add_argument(
        "--out", default=DEFAULT_OUT, help=f"output PEM path (default {DEFAULT_OUT})"
    )
    parser.add_argument("--force", action="store_true", help="overwrite existing file")
    args = parser.parse_args()

    if os.path.exists(args.out) and not args.force:
        print(f"REFUSING to overwrite existing key {args.out} (use --force)", file=sys.stderr)
        print(
            "Ротация ключа = пере-выпуск подписи для всей экосистемы — осознанно.", file=sys.stderr
        )
        return 1

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )

    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)

    jwk = JsonWebKey.import_key(pem, {"kty": "RSA", "use": "sig", "alg": "RS256"})
    jwk.ensure_kid()
    print(f"written: {args.out} (mode 0600)")
    print(f"kid: {jwk.kid}")
    print("public JWKS:")
    pub = jwk.as_dict(private=False)
    pub.setdefault("use", "sig")
    pub.setdefault("alg", "RS256")
    print(json.dumps({"keys": [pub]}, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
