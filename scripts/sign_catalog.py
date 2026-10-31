"""Owner tool: generate the catalog signing key and sign catalog releases (ed25519).

    python scripts/sign_catalog.py keygen KEYFILE        # writes private key, prints public key
    python scripts/sign_catalog.py sign KEYFILE CATALOG   # writes CATALOG.sig (base64)
    python scripts/sign_catalog.py verify PUBKEY_B64 CATALOG

Paste the printed public key into ``EMBEDDED_PUBLIC_KEY_B64`` in
``headwaters/core/catalog/trust.py``. Keep KEYFILE secret and out of the repo. Publish CATALOG
and CATALOG.sig side by side at the configured catalog URL.
"""

from __future__ import annotations

import argparse
import base64
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

_RAW = serialization.Encoding.Raw, serialization.PublicFormat.Raw


def _public_b64(key: Ed25519PrivateKey) -> str:
    return base64.b64encode(key.public_key().public_bytes(*_RAW)).decode("ascii")


def keygen(keyfile: Path) -> int:
    if keyfile.exists():
        sys.stderr.write(f"{keyfile} already exists; refusing to overwrite.\n")
        return 1
    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(
        serialization.Encoding.Raw,
        serialization.PrivateFormat.Raw,
        serialization.NoEncryption(),
    )
    keyfile.write_text(base64.b64encode(seed).decode("ascii") + "\n", encoding="ascii")
    sys.stdout.write(f"Public key (embed this): {_public_b64(key)}\n")
    return 0


def _load_private(keyfile: Path) -> Ed25519PrivateKey:
    seed = base64.b64decode(keyfile.read_text(encoding="ascii").strip(), validate=True)
    return Ed25519PrivateKey.from_private_bytes(seed)


def sign(keyfile: Path, catalog: Path) -> int:
    key = _load_private(keyfile)
    signature = base64.b64encode(key.sign(catalog.read_bytes())).decode("ascii")
    out = catalog.with_name(catalog.name + ".sig")
    out.write_text(signature + "\n", encoding="ascii")
    sys.stdout.write(f"Wrote {out}\n")
    return 0


def verify(public_b64: str, catalog: Path) -> int:
    from headwaters.core.catalog.trust import SignatureError
    from headwaters.core.catalog.trust import verify as verify_signature

    sig = catalog.with_name(catalog.name + ".sig").read_bytes()
    try:
        verify_signature(catalog.read_bytes(), sig, public_b64)
    except SignatureError as exc:
        sys.stderr.write(f"INVALID: {exc}\n")
        return 1
    sys.stdout.write("OK\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    sub = parser.add_subparsers(dest="command", required=True)
    p_keygen = sub.add_parser("keygen")
    p_keygen.add_argument("keyfile", type=Path)
    p_sign = sub.add_parser("sign")
    p_sign.add_argument("keyfile", type=Path)
    p_sign.add_argument("catalog", type=Path)
    p_verify = sub.add_parser("verify")
    p_verify.add_argument("public_key")
    p_verify.add_argument("catalog", type=Path)
    args = parser.parse_args(argv)
    if args.command == "keygen":
        return keygen(args.keyfile)
    if args.command == "sign":
        return sign(args.keyfile, args.catalog)
    return verify(args.public_key, args.catalog)


if __name__ == "__main__":
    raise SystemExit(main())
