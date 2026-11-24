"""Locust load test of the platform's real profiles (F29-15, #1191).

Four weighted user classes, each the way its client really calls the platform:

- ``WebAnalyst`` (5): the web's server-rendered customer and segment pages,
  with the session cookie of one ANALYST sign-in;
- ``FloorSeller`` (3): the Android path, JSON over the edge, recording sales
  with a fresh ``Idempotency-Key`` (and, now and then, replaying one) and
  reading products and stock;
- ``DesktopReports`` (2): the Electron path, the analytics-kpis reports in XML;
- ``OpsAdmin`` (1): the ops console's page and status, plus the users and
  audit reads an administrator makes.

Sign-in happens once per account, before any user starts, spaced
``MOSAIQ_LOCUST_SIGN_IN_INTERVAL`` seconds apart so that the edge's per-IP
sign-in limit (6 a minute, burst 5, on both ``/login`` and
``POST /api/auth/v1/sessions``) is never reached. The floor and desktop classes
take their accounts round-robin from ``MOSAIQ_LOCUST_ACCOUNTS_FILE``'s pools
(accounts.py), since every service limits each account to 120 requests a
minute; without a pool file, every user of a class shares its one account.
Access tokens live ten minutes, so one user per account rotates the pair
through ``/api/auth/v1/sessions/refresh``, which the edge does not count.

Configuration is read from the environment; see bench/locust/README.md.
Run it headless with the shape below (``LoadShape``); every request
is also written to ``<MOSAIQ_LOCUST_OUT>/requests.csv`` and every phase to
``phases.csv``, which ``report.py`` turns into the report.
"""

from __future__ import annotations

import csv
import itertools
import json
import os
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import requests
from locust import HttpUser, LoadTestShape, between, events, task

LOCAL_PASSWORD = "Password123!"
ACCESS_REFRESH_AFTER = 480
CSRF = re.compile(r'name="csrf_token" value="([^"]+)"')


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_list(name: str, default: str) -> list[str]:
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


API_URL = env("MOSAIQ_LOCUST_API_URL", "http://127.0.0.1:18191").rstrip("/")
WEB_URL = env("MOSAIQ_LOCUST_WEB_URL", "http://127.0.0.1:18192").rstrip("/")
CONSOLE_URL = env("MOSAIQ_LOCUST_CONSOLE_URL", "http://127.0.0.1:18193").rstrip("/")
INSECURE_TLS = env("MOSAIQ_LOCUST_INSECURE_TLS").lower() in {"1", "true", "yes"}
RUN_ID = env("MOSAIQ_LOCUST_RUN_ID") or datetime.now(UTC).strftime("%Y%m%d%H%M%S")
OUT = Path(env("MOSAIQ_LOCUST_OUT", f"bench/locust/results/{RUN_ID}"))
SIGN_IN_INTERVAL = float(env("MOSAIQ_LOCUST_SIGN_IN_INTERVAL", "10.5"))
STORE_IDS = [int(s) for s in env_list("MOSAIQ_LOCUST_STORE_IDS", "6,14,22,30")]
PRODUCT_IDS = [int(p) for p in env_list("MOSAIQ_LOCUST_PRODUCT_IDS", "1,2,3,4,5")]
CUSTOMER_IDS = env_list(
    "MOSAIQ_LOCUST_CUSTOMER_IDS",
    ",".join(f"00000000-0000-0000-0000-{n:012d}" for n in range(1, 31)),
)
SEGMENT_IDS = [int(s) for s in env_list("MOSAIQ_LOCUST_SEGMENT_IDS", "1,2,3,4,5,6")]
# Pages of 20 customers; past the last one the web redirects, a 302.
CUSTOMER_PAGES = int(env("MOSAIQ_LOCUST_CUSTOMER_PAGES", "2"))
CHANNEL_ID = int(env("MOSAIQ_LOCUST_CHANNEL_ID", "3"))
REPLAY_SHARE = float(env("MOSAIQ_LOCUST_REPLAY_SHARE", "0.05"))
# The reports' window: the desktop's default, a year back, ended yesterday
# (demand-coverage refuses a window that has not ended).
REPORT_TO = date.today() - timedelta(days=1)
REPORT_FROM = REPORT_TO - timedelta(days=365)


def is_local() -> bool:
    parsed = urlsplit(API_URL)
    return parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}


def account(role: str, seeded: str) -> tuple[str, str]:
    """The account of one class, from the environment.

    The local stack falls back to the seeded demonstration accounts; any other
    target must name its accounts, so no run reaches the instance with them.
    """
    email = env(f"MOSAIQ_LOCUST_{role}_EMAIL")
    password = os.environ.get(f"MOSAIQ_LOCUST_{role}_PASSWORD", "")
    if not email and is_local():
        email, password = seeded, LOCAL_PASSWORD
    if not email or not password:
        raise SystemExit(
            f"MOSAIQ_LOCUST_{role}_EMAIL and MOSAIQ_LOCUST_{role}_PASSWORD "
            "are required outside the local stack."
        )
    return email, password


def correlation_id() -> str:
    # The edge keeps a caller's id only when it is [0-9A-Za-z-]{1,64}.
    return f"locust-{RUN_ID}-{uuid.uuid4().hex[:16]}"[:64]


# Sign-in --------------------------------------------------------------------


class Pacer:
    """Spaces every sign-in of the run, whatever account it is for."""

    def __init__(self, interval: float):
        self.interval = interval
        self.last: float | None = None
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            if self.last is not None:
                time.sleep(max(0.0, self.interval - (time.monotonic() - self.last)))
            self.last = time.monotonic()


PACER = Pacer(SIGN_IN_INTERVAL)


def setup_session() -> requests.Session:
    session = requests.Session()
    session.verify = not INSECURE_TLS
    session.headers["User-Agent"] = "mosaiq-locust/F29-15"
    return session


@dataclass
class Credential:
    """One account's token pair, shared by every user of its class."""

    email: str
    password: str
    access: str = ""
    refresh: str = ""
    issued: float = 0.0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def sign_in(self) -> None:
        PACER.wait()
        reply = setup_session().post(
            API_URL + "/api/auth/v1/sessions",
            json={
                "email": self.email,
                "password": self.password,
                "device_label": f"F29-15 Locust {RUN_ID}",
            },
            headers={"X-Correlation-ID": correlation_id()},
            timeout=30,
        )
        if reply.status_code != 201:
            raise SystemExit(
                f"Sign-in as {self.email} answered {reply.status_code}: "
                f"{reply.text[:300]}"
            )
        self.adopt(reply.json())

    def adopt(self, body: dict) -> None:
        self.access = body["access_token"]
        self.refresh = body["refresh_token"]
        self.issued = time.monotonic()

    def token(self, client) -> str:
        """The access token, rotated by one user once it is eight minutes old."""
        if time.monotonic() - self.issued > ACCESS_REFRESH_AFTER:
            with self.lock:
                if time.monotonic() - self.issued > ACCESS_REFRESH_AFTER:
                    with client.post(
                        API_URL + "/api/auth/v1/sessions/refresh",
                        json={"refresh_token": self.refresh},
                        headers={"X-Correlation-ID": correlation_id()},
                        name="/api/auth/v1/sessions/refresh",
                        catch_response=True,
                    ) as reply:
                        if reply.status_code == 200:
                            self.adopt(reply.json())
                        else:
                            reply.failure(f"refresh {outcome(reply)}")
        return self.access


@dataclass
class WebLogin:
    email: str
    password: str
    cookies: dict = field(default_factory=dict)

    def sign_in(self) -> None:
        session = setup_session()
        page = session.get(WEB_URL + "/login", timeout=30)
        match = CSRF.search(page.text)
        if page.status_code != 200 or not match:
            raise SystemExit(f"The web's /login answered {page.status_code}.")
        PACER.wait()
        reply = session.post(
            WEB_URL + "/login",
            data={
                "csrf_token": match.group(1),
                "email": self.email,
                "password": self.password,
            },
            headers={"Origin": WEB_URL, "Referer": WEB_URL + "/login"},
            allow_redirects=False,
            timeout=30,
        )
        if reply.status_code != 302 or "/login" in reply.headers.get("Location", ""):
            raise SystemExit(
                f"Web sign-in as {self.email} answered {reply.status_code}."
            )
        self.cookies = session.cookies.get_dict()


@dataclass
class Fixtures:
    web: WebLogin | None = None
    floor: list[Credential] = field(default_factory=list)
    desktop: list[Credential] = field(default_factory=list)
    admin: Credential | None = None
    prices: dict[int, str] = field(default_factory=dict)


FIXTURES = Fixtures()
ASSIGNED = itertools.count()


def pool(name: str, seeded: str) -> list[Credential]:
    """A class's accounts: the pool file's, else the one account of the env.

    Every service limits each account (the token's `sub`) to 120 requests a
    minute; accounts.py makes pools so that limit does not stand in for the
    platform's capacity.
    """
    path = env("MOSAIQ_LOCUST_ACCOUNTS_FILE")
    if path:
        accounts = json.loads(Path(path).read_text()).get(name, [])
        if accounts:
            return [Credential(a["email"], a["password"]) for a in accounts]
    return [Credential(*account(name, seeded))]


def load_prices(credential: Credential) -> dict[int, str]:
    """The list price of each sale product, since a sale below it is refused."""
    session = setup_session()
    prices = {}
    for product_id in PRODUCT_IDS:
        reply = session.get(
            f"{API_URL}/api/products/v1/products/{product_id}",
            headers={
                "Authorization": "Bearer " + credential.access,
                "Accept": "application/json",
            },
            timeout=30,
        )
        if reply.status_code != 200:
            raise SystemExit(f"Product {product_id} answered {reply.status_code}.")
        prices[product_id] = reply.json()["list_price"]
    return prices


# Recording --------------------------------------------------------------------


class Recorder:
    """Every request and every phase of the run, for report.py."""

    def __init__(self) -> None:
        self.requests = None
        self.phases = None
        self.lock = threading.Lock()

    def open(self) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        self.requests = (OUT / "requests.csv").open("w", newline="")
        self.request_writer = csv.writer(self.requests)
        self.request_writer.writerow(
            ["t", "class", "name", "ms", "ok", "status", "error"]
        )
        self.phases = (OUT / "phases.csv").open("w", newline="")
        self.phase_writer = csv.writer(self.phases)
        self.phase_writer.writerow(["t", "phase", "users"])

    def request(self, row: list) -> None:
        if self.requests is not None:
            with self.lock:
                self.request_writer.writerow(row)

    def phase(self, name: str, users: int) -> None:
        if self.phases is not None:
            with self.lock:
                self.phase_writer.writerow([f"{time.time():.3f}", name, users])
                self.phases.flush()

    def close(self) -> None:
        for handle in (self.requests, self.phases):
            if handle is not None:
                handle.close()
        self.requests = self.phases = None


RECORDER = Recorder()


@events.request.add_listener
def on_request(
    request_type, name, response_time, response_length, exception, context, **_
):
    response = _.get("response")
    status = getattr(response, "status_code", 0) or 0
    RECORDER.request(
        [
            f"{time.time():.3f}",
            (context or {}).get("class", ""),
            f"{request_type} {name}",
            f"{response_time:.1f}",
            0 if exception else 1,
            status,
            str(exception)[:120] if exception else "",
        ]
    )


@events.init.add_listener
def on_init(environment, **_):
    """Sign in before the shape's clock starts, so step 1 keeps its length."""
    if environment.parsed_options is None or not environment.parsed_options.headless:
        raise SystemExit("Run headless: the sign-ins happen before the first step.")
    RECORDER.open()
    (OUT / "settings.txt").write_text(
        "\n".join(
            [
                f"run_id={RUN_ID}",
                f"api_url={API_URL}",
                f"web_url={WEB_URL}",
                f"console_url={CONSOLE_URL}",
                f"store_ids={','.join(map(str, STORE_IDS))}",
                f"product_ids={','.join(map(str, PRODUCT_IDS))}",
                f"sign_in_interval_s={SIGN_IN_INTERVAL}",
                f"steps={','.join(map(str, LoadShape.steps))}",
                f"step_seconds={LoadShape.step_seconds}",
                f"recovery_users={LoadShape.recovery_users}",
                f"recovery_seconds={LoadShape.recovery_seconds}",
                f"spawn_rate={LoadShape.spawn_rate}",
                "",
            ]
        )
    )
    FIXTURES.web = WebLogin(*account("ANALYST", "user2@mosaiq-demo.com"))
    FIXTURES.floor = pool("FLOOR", "user31@mosaiq-demo.com")
    FIXTURES.desktop = pool("DESKTOP", "user2@mosaiq-demo.com")
    FIXTURES.admin = Credential(*account("ADMIN", "admin@mosaiq-demo.com"))
    RECORDER.phase("sign-in", 0)
    FIXTURES.web.sign_in()
    for credential in [*FIXTURES.floor, *FIXTURES.desktop, FIXTURES.admin]:
        credential.sign_in()
    FIXTURES.prices = load_prices(FIXTURES.floor[0])
    with (OUT / "settings.txt").open("a") as handle:
        handle.write(f"floor_accounts={len(FIXTURES.floor)}\n")
        handle.write(f"desktop_accounts={len(FIXTURES.desktop)}\n")


@events.test_stop.add_listener
def on_test_stop(environment, **_):
    RECORDER.phase("end", 0)
    RECORDER.close()


# Users -------------------------------------------------------------------------


class Profile(HttpUser):
    abstract = True
    wait_time = between(1, 3)
    label = ""

    def context(self) -> dict:
        return {"class": self.label}

    def api(self, credential: Credential, accept: str) -> dict:
        return {
            "Authorization": "Bearer " + credential.token(self.client),
            "Accept": accept,
            "X-Correlation-ID": correlation_id(),
        }


ERROR_CODE = re.compile(rb'"code"\s*:\s*"([a-z_]+)"|<code>([a-z_]+)</code>')


def outcome(reply) -> str:
    """The status and the error envelope's code, as the report groups them."""
    match = ERROR_CODE.search(reply.content[:600] if reply.content else b"")
    code = (match.group(1) or match.group(2)).decode() if match else ""
    detail = ""
    if code and reply.content:
        field = re.search(rb'"field"\s*:\s*"([^"]+)"', reply.content[:600])
        detail = f" ({field.group(1).decode()})" if field else ""
    return f"{reply.status_code} {code}{detail}".strip()


def expect(reply, statuses: tuple[int, ...], media: str) -> None:
    content_type = reply.headers.get("Content-Type", "")
    if reply.status_code not in statuses:
        reply.failure(outcome(reply))
    elif media and not content_type.startswith(media):
        reply.failure(f"content type {content_type!r}")
    elif media == "application/xml" and b"<Error" in reply.content[:300]:
        reply.failure("XML error envelope")
    else:
        reply.success()


class WebAnalyst(Profile):
    """An analyst browsing customers and segments in the web (HTML)."""

    weight = 5
    host = WEB_URL
    label = "web-analyst"

    def on_start(self) -> None:
        self.client.cookies.update(FIXTURES.web.cookies)

    def page(self, path: str, name: str) -> None:
        with self.client.get(
            path,
            name=name,
            allow_redirects=False,
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "text/html")

    @task(4)
    def customers(self) -> None:
        page = random.randint(1, CUSTOMER_PAGES)
        self.page(f"/catalog/customers?page={page}", "/catalog/customers")

    @task(2)
    def search_customers(self) -> None:
        term = random.choice(["demo", "ana", "lu", "mar", "garc", "1"])
        self.page(f"/catalog/customers?q={term}", "/catalog/customers?q=")

    @task(3)
    def customer(self) -> None:
        self.page(
            f"/catalog/customers/{random.choice(CUSTOMER_IDS)}",
            "/catalog/customers/{id}",
        )

    @task(2)
    def segments(self) -> None:
        self.page("/catalog/segments", "/catalog/segments")

    @task(2)
    def segment(self) -> None:
        self.page(
            f"/catalog/segments/{random.choice(SEGMENT_IDS)}", "/catalog/segments/{id}"
        )

    @task(1)
    def dashboard(self) -> None:
        self.page("/segmentation-dashboard/", "/segmentation-dashboard/")


class FloorSeller(Profile):
    """Floor staff on the Android path: JSON, sales with an Idempotency-Key."""

    weight = 3
    host = API_URL
    label = "floor-android"

    def on_start(self) -> None:
        self.last: tuple[str, dict] | None = None
        self.credential = FIXTURES.floor[next(ASSIGNED) % len(FIXTURES.floor)]

    def sale_body(self) -> dict:
        products = random.sample(
            PRODUCT_IDS, k=min(len(PRODUCT_IDS), random.randint(1, 3))
        )
        return {
            "customer_id": random.choice(CUSTOMER_IDS),
            "store_id": random.choice(STORE_IDS),
            "channel_id": CHANNEL_ID,
            # Rung up a moment before it is sent. A sale dated even a fraction
            # of a second after the service's clock is 422 future_dated, and
            # the load generator's clock is not the server's.
            "occurred_at": (datetime.now(UTC) - timedelta(seconds=2)).isoformat(
                timespec="seconds"
            ),
            "lines": [
                {
                    "product_id": product_id,
                    "quantity": 1,
                    "unit_price": FIXTURES.prices[product_id],
                }
                for product_id in products
            ],
        }

    @task(3)
    def record_sale(self) -> None:
        replay = self.last is not None and random.random() < REPLAY_SHARE
        key, body = self.last if replay else (str(uuid.uuid4()), self.sale_body())
        headers = self.api(self.credential, "application/json")
        headers["Idempotency-Key"] = key
        with self.client.post(
            "/api/sales/v1/sales",
            json=body,
            headers=headers,
            name="/api/sales/v1/sales" + (" (replay)" if replay else ""),
            catch_response=True,
        ) as reply:
            if replay and reply.headers.get("Idempotent-Replayed") != "true":
                reply.failure(f"replay answered {outcome(reply)} without the header")
            else:
                expect(reply, (200,) if replay else (201,), "application/json")
            # Only a stored sale can be replayed: a refused one was never kept.
            if reply.status_code in (200, 201):
                self.last = (key, body)

    @task(1)
    def products(self) -> None:
        with self.client.get(
            f"/api/products/v1/products?page={random.randint(1, 2)}&size=20",
            headers=self.api(self.credential, "application/json"),
            name="/api/products/v1/products",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/json")

    @task(1)
    def stock(self) -> None:
        with self.client.get(
            f"/api/stock/v1/stock-levels?store_id={random.choice(STORE_IDS)}",
            headers=self.api(self.credential, "application/json"),
            name="/api/stock/v1/stock-levels",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/json")


class DesktopReports(Profile):
    """The desktop reading the analytics-kpis reports, in XML only."""

    weight = 2
    host = API_URL
    label = "desktop-xml"

    def on_start(self) -> None:
        self.credential = FIXTURES.desktop[next(ASSIGNED) % len(FIXTURES.desktop)]

    window = f"from={REPORT_FROM}&to={REPORT_TO}&page=1&size=50"

    def report(self, kpi: str, query: str = "") -> None:
        with self.client.get(
            f"/api/analytics-kpis/v1/kpi-reports/{kpi}?{self.window}{query}",
            headers=self.api(self.credential, "application/xml"),
            name=f"/api/analytics-kpis/v1/kpi-reports/{kpi}",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/xml")

    @task(3)
    def net_sales_by_store(self) -> None:
        self.report("net-sales", "&group_by=store")

    @task(2)
    def net_sales_by_segment_month(self) -> None:
        with self.client.get(
            "/api/analytics-kpis/v1/kpi-reports/net-sales?"
            f"{self.window}&group_by=segment,period&granularity=month",
            headers=self.api(self.credential, "application/xml"),
            name="/api/analytics-kpis/v1/kpi-reports/net-sales (segment, month)",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/xml")

    @task(2)
    def spend(self) -> None:
        self.report("spend", "&group_by=segment")

    @task(1)
    def retention(self) -> None:
        self.report("retention")

    @task(1)
    def migrations(self) -> None:
        self.report("migrations")

    @task(1)
    def demand_coverage(self) -> None:
        self.report("demand-coverage")

    @task(1)
    def definitions(self) -> None:
        with self.client.get(
            "/api/analytics-kpis/v1/kpi-reports/definitions?page=1&size=50",
            headers=self.api(self.credential, "application/xml"),
            name="/api/analytics-kpis/v1/kpi-reports/definitions",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/xml")


class OpsAdmin(Profile):
    """The administrator watching the ops console and reading users and audit."""

    weight = 1
    host = API_URL
    label = "ops-admin"

    @task(3)
    def status(self) -> None:
        with self.client.get(
            CONSOLE_URL + "/status.json",
            name="console /status.json",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/json")

    @task(1)
    def console_page(self) -> None:
        with self.client.get(
            CONSOLE_URL + "/",
            name="console /",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "text/html")

    @task(1)
    def users(self) -> None:
        with self.client.get(
            "/api/users/v1/users?page=1&size=20",
            headers=self.api(FIXTURES.admin, "application/json"),
            name="/api/users/v1/users",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/json")

    @task(1)
    def audit(self) -> None:
        with self.client.get(
            "/api/audit-trail/v1/audit-entries?page=1&size=20",
            headers=self.api(FIXTURES.admin, "application/json"),
            name="/api/audit-trail/v1/audit-entries",
            catch_response=True,
        ) as reply:
            expect(reply, (200,), "application/json")


# Load shape --------------------------------------------------------------------


class LoadShape(LoadTestShape):
    """Steps up to overload, then drops to a light load to time recovery."""

    steps = [int(s) for s in env_list("MOSAIQ_LOCUST_STEPS", "10,20,40,80,120,160")]
    step_seconds = int(env("MOSAIQ_LOCUST_STEP_SECONDS", "60"))
    recovery_users = int(env("MOSAIQ_LOCUST_RECOVERY_USERS", "10"))
    recovery_seconds = int(env("MOSAIQ_LOCUST_RECOVERY_SECONDS", "180"))
    spawn_rate = float(env("MOSAIQ_LOCUST_SPAWN_RATE", "10"))

    def __init__(self) -> None:
        super().__init__()
        self.current = None

    def tick(self):
        elapsed = self.get_run_time()
        index = int(elapsed // self.step_seconds)
        if index < len(self.steps):
            phase, users = f"step-{index + 1}", self.steps[index]
            rate = self.spawn_rate
        elif elapsed < len(self.steps) * self.step_seconds + self.recovery_seconds:
            # Users above the recovery count stop at once.
            phase, users, rate = "recovery", self.recovery_users, 1000.0
        else:
            return None
        if phase != self.current:
            self.current = phase
            RECORDER.phase(phase, users)
        return users, rate
