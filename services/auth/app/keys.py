"""Load the sole signing key and its public JWK Set once at process start."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

PUBLIC_RSA_MEMBERS = ("kty", "kid", "alg", "use", "n", "e")


def _b64url(number: int) -> str:
    raw = number.to_bytes((number.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class SigningMaterial:
    private_key: rsa.RSAPrivateKey
    kid: str
    public_jwks: dict[str, list[dict[str, Any]]]


def load_signing_material(
    private_key_path: str | Path, jwks_path: str | Path, kid: str
) -> SigningMaterial:
    """Load and cross-check the private key and public JWKS.

    The returned JWK Set is projected onto the contract's public RSA members,
    so private members can never be served even if a mounted file is malformed.
    """

    if not kid:
        raise RuntimeError("AUTH_KID must name the configured signing key.")
    try:
        private_bytes = Path(private_key_path).read_bytes()
        private_key = serialization.load_pem_private_key(private_bytes, password=None)
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError(
            f"AUTH_PRIVATE_KEY_PATH could not be loaded: {private_key_path}"
        ) from error
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise RuntimeError("AUTH_PRIVATE_KEY_PATH must contain an RSA private key.")

    try:
        document = json.loads(Path(jwks_path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as error:
        raise RuntimeError(
            f"AUTH_JWKS_PATH could not be loaded: {jwks_path}"
        ) from error
    raw_keys = document.get("keys") if isinstance(document, dict) else None
    if not isinstance(raw_keys, list):
        raise RuntimeError("AUTH_JWKS_PATH must contain a JWK Set with a keys array.")

    public_keys = []
    for value in raw_keys:
        if not isinstance(value, dict):
            continue
        if not all(member in value for member in PUBLIC_RSA_MEMBERS):
            continue
        if value.get("kty") != "RSA" or value.get("alg") != "RS256":
            continue
        if value.get("use") != "sig":
            continue
        public_keys.append({member: value[member] for member in PUBLIC_RSA_MEMBERS})

    numbers = private_key.public_key().public_numbers()
    expected_n = _b64url(numbers.n)
    expected_e = _b64url(numbers.e)
    match = next((value for value in public_keys if value["kid"] == kid), None)
    if match is None or match["n"] != expected_n or match["e"] != expected_e:
        raise RuntimeError(
            "The public half of AUTH_PRIVATE_KEY_PATH is not present in "
            "AUTH_JWKS_PATH under AUTH_KID."
        )
    return SigningMaterial(private_key, kid, {"keys": public_keys})
