"""Test account pools for the Locust run (F29-15, #1191).

    python3 bench/locust/accounts.py create  <file> [--floor 12] [--desktop 12]
    python3 bench/locust/accounts.py retire  <file>

Every service limits each account to RATE_LIMIT_REQUESTS (120) requests a
RATE_LIMIT_WINDOW_SECONDS (60) per service, keyed on the token's `sub`. Many
virtual users on one account measure that limit, not the platform. A real
floor has one account per person, so the run spreads each API class over a
pool of accounts.

`create` signs in once as the administrator (MOSAIQ_LOCUST_ADMIN_EMAIL and
_PASSWORD, or the seeded administrator on the local stack), then, through
users-service only, creates `--floor` SALES_ASSOCIATE accounts assigned to
MOSAIQ_LOCUST_STORE_IDS and `--desktop` ANALYST accounts, each with a random
password. It writes them to `<file>` (mode 0600, JSON), which the locustfile
reads from MOSAIQ_LOCUST_ACCOUNTS_FILE. The file holds passwords: keep it
outside the repository and delete it after the run.

`retire` deactivates every account in `<file>`, through users-service, and
keeps the file so the run's rows can still be traced to them. There is exactly
one administrator, so the admin class never has a pool.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

API_URL = os.environ.get("MOSAIQ_LOCUST_API_URL", "http://127.0.0.1:18191").rstrip("/")
INSECURE_TLS = os.environ.get("MOSAIQ_LOCUST_INSECURE_TLS", "").lower() in {
    "1",
    "true",
    "yes",
}
STORE_IDS = [
    int(s)
    for s in os.environ.get("MOSAIQ_LOCUST_STORE_IDS", "6,14,22,30").split(",")
    if s.strip()
]
SIGN_IN_INTERVAL = float(os.environ.get("MOSAIQ_LOCUST_SIGN_IN_INTERVAL", "10.5"))


def call(method: str, path: str, body: dict | None = None, token: str = "") -> dict:
    headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        headers["Authorization"] = "Bearer " + token
    request = urllib.request.Request(
        API_URL + path,
        data=json.dumps(body).encode() if body is not None else None,
        method=method,
        headers=headers,
    )
    context = None
    if INSECURE_TLS:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(request, timeout=30, context=context) as reply:
            raw = reply.read()
    except urllib.error.HTTPError as error:
        raise SystemExit(
            f"{method} {path} answered {error.code}: {error.read()[:300]!r}"
        ) from None
    return json.loads(raw) if raw else {}


def admin_token() -> str:
    email = os.environ.get("MOSAIQ_LOCUST_ADMIN_EMAIL", "")
    password = os.environ.get("MOSAIQ_LOCUST_ADMIN_PASSWORD", "")
    local = urlsplit(API_URL).hostname in {"127.0.0.1", "localhost"}
    if not email and local:
        email, password = "admin@mosaiq-demo.com", "Password123!"
    if not email or not password:
        raise SystemExit("MOSAIQ_LOCUST_ADMIN_EMAIL and _PASSWORD are required.")
    body = {"email": email, "password": password, "device_label": "F29-15 accounts"}
    return call("POST", "/api/auth/v1/sessions", body)["access_token"]


def create(args: argparse.Namespace) -> int:
    target = Path(args.file)
    if target.exists():
        raise SystemExit(f"{target} exists; retire and remove it first.")
    token = admin_token()
    run = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    pools: dict[str, list[dict]] = {"FLOOR": [], "DESKTOP": []}
    for pool, role, count in (
        ("FLOOR", "SALES_ASSOCIATE", args.floor),
        ("DESKTOP", "ANALYST", args.desktop),
    ):
        for n in range(1, count + 1):
            email = f"locust-{run}-{pool.lower()}-{n:02d}@{args.domain}"
            password = "Locust-" + secrets.token_urlsafe(18)
            user = call(
                "POST",
                "/api/users/v1/users",
                {
                    "name": f"Locust {pool.title()} {n:02d}",
                    "email": email,
                    "role_code": role,
                    "password": password,
                },
                token,
            )
            if role == "SALES_ASSOCIATE":
                call(
                    "PUT",
                    f"/api/users/v1/store-assignments/{user['user_id']}",
                    {"store_ids": STORE_IDS},
                    token,
                )
            pools[pool].append(
                {"user_id": user["user_id"], "email": email, "password": password}
            )
    target.touch(mode=0o600)
    target.write_text(json.dumps(pools, indent=2) + "\n")
    print(
        f"accounts: {args.floor} floor and {args.desktop} desktop accounts in {target}"
    )
    return 0


def retire(args: argparse.Namespace) -> int:
    pools = json.loads(Path(args.file).read_text())
    token = admin_token()
    for accounts in pools.values():
        for account in accounts:
            call(
                "POST",
                f"/api/users/v1/users/{account['user_id']}/deactivation",
                None,
                token,
            )
    print(f"accounts: deactivated {sum(map(len, pools.values()))} accounts")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create")
    c.add_argument("file")
    c.add_argument("--floor", type=int, default=12)
    c.add_argument("--desktop", type=int, default=12)
    c.add_argument("--domain", default="mosaiq-demo.com")
    c.set_defaults(run=create)
    r = sub.add_parser("retire")
    r.add_argument("file")
    r.set_defaults(run=retire)
    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
