"""ed25519 verification of the curated catalog.

The catalog file is signed byte-for-byte; the detached signature is base64 text. The public key
is embedded in the app (``EMBEDDED_PUBLIC_KEY_B64``, base64 of the raw 32-byte key). The owner
generates the key pair and signs releases with ``scripts/sign_catalog.py``; the private key
never lives in this repo.
"""

from __future__ import annotations

import base64

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

# Owner: run ``python scripts/sign_catalog.py keygen`` and paste the printed public key here.
# While empty, no downloaded catalog can be accepted (the bundled seed is still used).
EMBEDDED_PUBLIC_KEY_B64 = ""


class SignatureError(ValueError):
    """The catalog signature is missing, malformed or does not match the data."""


def verify(data: bytes, signature_b64: bytes | str, public_key_b64: str) -> None:
    """Raise ``SignatureError`` unless ``signature_b64`` is a valid signature of ``data``."""
    if not public_key_b64:
        raise SignatureError("no catalog public key is embedded in this build")
    raw_sig = (
        signature_b64.encode("ascii", "replace")
        if isinstance(signature_b64, str)
        else signature_b64
    )
    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key_b64, validate=True))
        signature = base64.b64decode(raw_sig.strip(), validate=True)
    except ValueError as exc:
        raise SignatureError(f"malformed key or signature: {exc}") from exc
    try:
        key.verify(signature, data)
    except InvalidSignature as exc:
        raise SignatureError("signature does not match the catalog data") from exc
