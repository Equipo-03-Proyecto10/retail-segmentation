"""Throwaway RS256 keys and tokens for the conformance suite (F13-09).

Standard library plus the `openssl` command, so the suite adds no dependency.
The key is generated per run by stack.sh and is never auth-service's: only its
public half reaches the image, as the mounted JWKS (ADR-0036).

    python3 tests/conformance/tokens.py jwks <private.pem> <kid>
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import subprocess
import sys
from pathlib import Path


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _openssl(*args: str, data: bytes | None = None) -> bytes:
    return subprocess.run(
        ["openssl", *args], input=data, capture_output=True, check=True
    ).stdout


def public_pem(private_key: Path) -> bytes:
    return _openssl("rsa", "-in", str(private_key), "-pubout")


def jwks(private_key: Path, kid: str) -> dict:
    modulus = _openssl("rsa", "-in", str(private_key), "-noout", "-modulus")
    n = bytes.fromhex(modulus.decode("ascii").strip().removeprefix("Modulus="))
    # `openssl genrsa` always uses the public exponent 65537.
    key = {"kty": "RSA", "use": "sig", "alg": "RS256", "kid": kid}
    return {"keys": [key | {"n": b64url(n), "e": b64url((65537).to_bytes(3, "big"))}]}


def _segments(header: dict, claims: dict) -> bytes:
    encode = json.dumps
    return (
        b64url(encode(header).encode()) + "." + b64url(encode(claims).encode())
    ).encode("ascii")


def sign_rs256(header: dict, claims: dict, private_key: Path) -> str:
    signing_input = _segments(header, claims)
    signature = _openssl(
        "dgst", "-sha256", "-sign", str(private_key), data=signing_input
    )
    return signing_input.decode("ascii") + "." + b64url(signature)


def sign_hs256(header: dict, claims: dict, secret: bytes) -> str:
    signing_input = _segments(header, claims)
    signature = hmac.new(secret, signing_input, hashlib.sha256).digest()
    return signing_input.decode("ascii") + "." + b64url(signature)


def unsigned(header: dict, claims: dict) -> str:
    return _segments(header, claims).decode("ascii") + "."


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "jwks":
        sys.exit("usage: tokens.py jwks <private.pem> <kid>")
    print(json.dumps(jwks(Path(sys.argv[2]), sys.argv[3])))
