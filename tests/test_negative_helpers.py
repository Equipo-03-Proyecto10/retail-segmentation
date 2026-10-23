"""The negative suite's token forging, checked without a stack (F18-04, #459).

tests/negative/ forges tokens from a live one. A forged token that fails for
the wrong reason would make its case pass vacuously, so each forgery is checked
here to differ from the live token in the one way its case names.
"""

from __future__ import annotations

import http.client
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conformance import tokens
from tests.negative import stack

pytestmark = pytest.mark.skipif(
    shutil.which("openssl") is None, reason="the helpers sign with the openssl command"
)

CLAIMS = {"iss": "https://auth.invalid/", "aud": "mosaiq-api", "sid": "s", "exp": 99}


@pytest.fixture(scope="module")
def key(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("keys") / "key.pem"
    subprocess.run(
        ["openssl", "genrsa", "-out", str(path), "2048"],
        check=True,
        capture_output=True,
    )
    return path


@pytest.fixture(scope="module")
def live(key: Path) -> str:
    header = {"alg": "RS256", "typ": "JWT", "kid": "k"}
    return tokens.sign_rs256(header, CLAIMS, key)


def test_the_reconstructed_public_pem_is_the_one_openssl_writes(key: Path) -> None:
    # The HS256 case keys an HMAC with the verifier's own public key; any other
    # text would be refused for the wrong reason.
    jwk = tokens.jwks(key, "k")["keys"][0]
    assert stack.public_pem(jwk) == tokens.public_pem(key)


def test_an_hs256_forgery_keeps_every_claim_and_only_changes_the_algorithm(
    key: Path, live: str
) -> None:
    jwk = tokens.jwks(key, "k")["keys"][0]
    forged = stack.hs256_with_public_key(live, jwk)

    assert stack.header_of(forged) == stack.header_of(live) | {"alg": "HS256"}
    assert stack.claims_of(forged) == stack.claims_of(live)
    # It verifies as an HMAC under the public PEM, as an attacker's would.
    signing_input, _, signature = forged.rpartition(".")
    expected = tokens.sign_hs256(
        stack.header_of(forged), stack.claims_of(forged), tokens.public_pem(key)
    )
    assert expected == signing_input + "." + signature


def test_alg_none_keeps_every_claim_and_carries_no_signature(live: str) -> None:
    forged = stack.unsigned(live)

    assert stack.header_of(forged)["alg"] == "none"
    assert stack.claims_of(forged) == stack.claims_of(live)
    assert forged.endswith(".")


def test_a_tampered_signature_changes_only_the_signature(live: str) -> None:
    forged = stack.tampered_signature(live)

    assert forged != live
    assert forged.split(".")[:2] == live.split(".")[:2]
    assert len(forged.split(".")[2]) == len(live.split(".")[2])


def test_a_resigned_token_verifies_under_the_key_with_the_changed_claim(
    key: Path, live: str
) -> None:
    forged = stack.resigned(live, (key, "k"), iss="https://issuer.invalid/")

    assert stack.claims_of(forged) == stack.claims_of(live) | {
        "iss": "https://issuer.invalid/"
    }
    assert stack.header_of(forged)["alg"] == "RS256"
    signing_input, _, signature = forged.rpartition(".")
    resigned_again = tokens.sign_rs256(
        stack.header_of(forged), stack.claims_of(forged), key
    )
    assert resigned_again == forged
    assert signing_input and signature


def test_the_evidence_document_keeps_the_other_targets_sections(
    tmp_path: Path,
) -> None:
    path = tmp_path / "evidence.md"
    local = stack.Evidence("Local stack")
    reply = stack.Reply(401, http.client.HTTPMessage(), b"")
    local.check("auth", "a case", reply, 401)
    local.write("http://127.0.0.1:8080", path)
    instance = stack.Evidence("Instance")
    instance.record("auth", "another case", "x", "y")
    instance.write("https://example.test", path)
    local.write("http://127.0.0.1:8080", path)

    text = path.read_text()
    assert text.count("## Local stack") == 1
    assert text.count("## Instance") == 1
    assert text.index("## Local stack") < text.index("## Instance")
    assert "1 held, 0 did not" in text
    assert "0 held, 1 did not" in text
    assert text.startswith("# F18-04")
