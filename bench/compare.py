"""Fairness rule 1 of ADR-0042: both builds answer a fixed sample identically.

Sends the same 20 requests per format to products-service and to the twin,
with the same token and the same `X-Correlation-ID`, and compares the status,
the parsed JSON, and the XML after `xmllint --c14n`. Prints each difference and
exits 1 on any; prints one summary line otherwise.

    python3 bench/compare.py <products-url> <twin-url>

Reads CONFORMANCE_PRIVATE_KEY, CONFORMANCE_KID, CONFORMANCE_REDIS_STATE and
REDIS_OPS_PASSWORD, as the conformance suite does.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tests.conformance.harness import Sessions, Signer  # noqa: E402

FIFTY_ONE = ",".join(str(i) for i in range(1, 52))
SAMPLE = [
    "/v1/products",
    "/v1/products?page=2&size=5",
    "/v1/products?page=500&size=20",
    "/v1/products?q=demo",
    "/v1/products?q=PREMIUM%20milk&size=50",
    "/v1/products?q=%25",
    "/v1/products?sku=SKU-00031",
    "/v1/products?category_id=4&size=100",
    "/v1/products?active=false&size=100",
    "/v1/products?ids=1,2,3,40,100001,100017",
    "/v1/products?ids=5,3,5,1",
    "/v1/products/3",
    "/v1/products/100017",
    "/v1/products/999999",
    "/v1/products?q=",
    "/v1/products?category_id=x",
    "/v1/products?active=yes",
    "/v1/products?ids=0",
    "/v1/products?ids=" + FIFTY_ONE,
    "/v1/products?size=101",
]
CORRELATION = "bench-compare"


def fetch(base: str, path: str, accept: str, token: str) -> tuple[int, bytes]:
    url = urlsplit(base)
    connection = http.client.HTTPConnection(url.hostname, url.port, timeout=15)
    try:
        connection.request(
            "GET",
            path,
            headers={
                "Accept": accept,
                "Authorization": "Bearer " + token,
                "X-Correlation-ID": CORRELATION,
            },
        )
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def canonical_xml(body: bytes) -> bytes:
    return subprocess.run(
        ["xmllint", "--c14n", "-"], input=body, capture_output=True, check=True
    ).stdout


def main(products: str, twin: str) -> int:
    signer = Signer(
        Path(os.environ["CONFORMANCE_PRIVATE_KEY"]), os.environ["CONFORMANCE_KID"]
    )
    sessions = Sessions(os.environ["CONFORMANCE_REDIS_STATE"])
    token = signer.sign(signer.claims("ANALYST", sessions.open()))
    differences = 0
    for accept in ("application/json", "application/xml"):
        for path in SAMPLE:
            flask = fetch(products, path, accept, token)
            rust = fetch(twin, path, accept, token)
            if accept == "application/json":
                same = flask[0] == rust[0] and json.loads(flask[1]) == json.loads(
                    rust[1]
                )
            else:
                same = flask[0] == rust[0] and canonical_xml(flask[1]) == canonical_xml(
                    rust[1]
                )
            if not same:
                differences += 1
                print(f"DIFFERENT {accept} {path}")
                print(f"  products: {flask[0]} {flask[1][:400]!r}")
                print(f"  twin:     {rust[0]} {rust[1][:400]!r}")
    if differences:
        print(f"compare: {differences} of {2 * len(SAMPLE)} answers differ")
        return 1
    print(f"compare: {len(SAMPLE)} requests x 2 formats answered identically")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit("usage: compare.py <products-url> <twin-url>")
    sys.exit(main(sys.argv[1], sys.argv[2]))
