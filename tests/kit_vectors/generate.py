"""Generate public verification vectors for mosaiq-kit.

The throwaway signing key exists only in this process. Only its public JWK and
the resulting compact tokens are written to the repository.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "packages/kit-vectors"
ISSUER = "https://kit-vectors.invalid/auth"
KEY_ID = "kit-vector-key"
SUBJECT = "3b6a2b8b-3e24-4ba6-9ef9-fb3e6b4c9ad1"
LIVE_SESSION = "087357a4-7d4b-4bdd-936d-8e403790303e"
REVOKED_SESSION = "7b3cb5d4-cf3b-4fd8-b63a-69263ec07381"
FUTURE = 4_102_444_800


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _compact(header: dict[str, object], payload: dict[str, object], key: bytes) -> str:
    encoded_header = _b64(json.dumps(header, separators=(",", ":")).encode())
    encoded_payload = _b64(json.dumps(payload, separators=(",", ":")).encode())
    signing_input = (encoded_header + "." + encoded_payload).encode()
    signature = hmac.new(key, signing_input, hashlib.sha256).digest()
    return encoded_header + "." + encoded_payload + "." + _b64(signature)


def _claims(**changes: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": "mosaiq-api",
        "exp": FUTURE,
        "sub": SUBJECT,
        "sid": LIVE_SESSION,
        "role": "ROLE_READER",
    }
    claims.update(changes)
    return claims


def main() -> None:
    private_key = rsa.generate_private_key(public_exponent=65_537, key_size=2048)
    public_key = private_key.public_key()
    public_numbers = public_key.public_numbers()
    jwk = {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": KEY_ID,
        "n": _b64(public_numbers.n.to_bytes(256, "big")),
        "e": _b64(public_numbers.e.to_bytes(3, "big")),
    }
    public_pem = public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    def signed(claims: dict[str, object], kid: str = KEY_ID) -> str:
        return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": kid})

    valid_reader = signed(_claims())
    valid_writer = signed(_claims(role="ROLE_WRITER"))
    payload = valid_reader.split(".")[1]
    padding = "=" * (-len(payload) % 4)
    tampered_claims = json.loads(base64.urlsafe_b64decode(payload + padding))
    tampered_claims["role"] = "ROLE_WRITER"
    tampered_payload = _b64(json.dumps(tampered_claims, separators=(",", ":")).encode())
    token_parts = valid_reader.split(".")
    tampered = token_parts[0] + "." + tampered_payload + "." + token_parts[2]

    tokens = {
        "no_token": "",
        "alg_none": _b64(b'{"alg":"none","kid":"kit-vector-key","typ":"JWT"}')
        + "."
        + _b64(json.dumps(_claims(), separators=(",", ":")).encode())
        + ".",
        "hmac_with_public_key": _compact(
            {"alg": "HS256", "kid": KEY_ID, "typ": "JWT"},
            _claims(),
            public_pem,
        ),
        "wrong_aud": signed(_claims(aud="another-api")),
        "wrong_iss": signed(_claims(iss="https://another.invalid/auth")),
        "expired": signed(_claims(exp=1_600_000_000)),
        "tampered_payload": tampered,
        "unknown_kid": signed(_claims(), kid="unknown-vector-key"),
        "revoked_session": signed(_claims(sid=REVOKED_SESSION)),
        "valid_reader": valid_reader,
        "valid_writer": valid_writer,
    }
    expected = {
        "no_token": {"status": 401, "code": "invalid_token"},
        "alg_none": {"status": 401, "code": "invalid_token"},
        "hmac_with_public_key": {"status": 401, "code": "invalid_token"},
        "wrong_aud": {"status": 401, "code": "invalid_token"},
        "wrong_iss": {"status": 401, "code": "invalid_token"},
        "expired": {"status": 401, "code": "invalid_token"},
        "tampered_payload": {"status": 401, "code": "invalid_token"},
        "unknown_kid": {"status": 401, "code": "invalid_token"},
        "revoked_session": {"status": 401, "code": "session_revoked"},
        "auth_unavailable": {"status": 503, "code": "auth_unavailable"},
        "forbidden_role": {"status": 403, "code": "forbidden_role"},
        "database_unavailable": {"status": 503, "code": "database_unavailable"},
        "valid_reader": {"status": 200, "code": None},
        "valid_writer": {"status": 200, "code": None},
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "jwks.json").write_text(
        json.dumps({"keys": [jwk]}, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "tokens.json").write_text(
        json.dumps(tokens, indent=2) + "\n", encoding="utf-8"
    )
    (OUTPUT / "expected.json").write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
