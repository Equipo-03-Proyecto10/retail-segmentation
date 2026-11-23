"""The pure parts of the acceptance collectors (tests/acceptance/, #888, #891, #462).

What they say about a live system is shown by running them (the runbooks in
docs/evidence/f29-03-*.md and f29-06-*.md, docs/demo-distributed.md); these
cases pin what they do with what they read: masking, bind parameters, page
parsing and the counts. The rehearsal's preconditions (demo.py) are also run
against a loopback gateway that answers with the contracts' own examples.
"""

from __future__ import annotations

import argparse
import json
import shutil
import threading
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from tests.acceptance import demo, f24_08
from tests.acceptance.evidence import (
    JWT,
    Database,
    Masker,
    Recorder,
    UnmaskedSecretError,
    contract_version,
    to_bind,
)
from tests.acceptance.f29_03 import parse_profile, stock_on_page
from tests.acceptance.f29_06 import evidence_counts, is_local, junit_counts
from tests.negative.stack import ROOT, Edge, implemented, registry_names

TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiIxMjMifQ.c2lnbmF0dXJl"
MARKETING_TOKEN = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiI0NTYifQ.c2lnbmF0dXJl"


def test_a_token_is_masked_in_headers_json_and_forms() -> None:
    masker = Masker()
    assert masker.header("Authorization", "Bearer " + TOKEN) == "Bearer <masked #1>"
    body = masker.body(json.dumps({"access_token": TOKEN, "user": {"password": "x"}}))
    assert TOKEN not in body and '"x"' not in body
    form = masker.body(b"email=a%40b.test&password=secret&csrf_token=abc")
    assert "secret" not in form and "abc" not in form and "a@b.test" in form
    assert not JWT.search(masker.body("free text with " + TOKEN))


def test_the_same_secret_keeps_its_mark() -> None:
    masker = Masker()
    assert masker.mark("a") == masker.mark("a") == "<masked #1>"
    assert masker.mark("b") == "<masked #2>"


def test_write_refuses_evidence_that_still_holds_a_token(tmp_path) -> None:
    rec = Recorder(tmp_path, "t", "t")
    rec.add_rows("leak", TOKEN)
    with pytest.raises(UnmaskedSecretError):
        rec.write()
    assert not (tmp_path / "rows.md").exists()


def test_write_reports_whether_every_check_held(tmp_path) -> None:
    rec = Recorder(tmp_path, "t", "t")
    assert rec.check("c", "equal", 1, "1")
    assert not rec.check("c", "differs", 1, 2)
    assert rec.write() is False
    summary = (tmp_path / "summary.md").read_text()
    assert "2 checks, 1 held, 1 did not." in summary


def test_placeholders_become_bind_parameters() -> None:
    assert to_bind("a = %s AND b = %s") == "a = $1 AND b = $2"
    script = Database.bind_script("SELECT %s, %s;", 2)
    assert "SELECT $1, $2\n\\bind :p1 :p2\n\\g" in script
    assert script.startswith("BEGIN READ ONLY;") and script.endswith("ROLLBACK;\n")


def test_values_travel_as_arguments_quoted_only_for_a_remote_shell() -> None:
    local = Database("docker exec -i db")
    assert local.command(["-v", "p1=O'Brien; x"]) == [
        "docker", "exec", "-i", "db", "-v", "p1=O'Brien; x",
    ]  # fmt: skip
    remote = Database("ssh host sudo -n -u postgres")
    assert remote.remote_shell
    assert remote.command(["-v", "p1=a b"])[-1] == "'p1=a b'"


def test_the_profile_page_figures_are_read() -> None:
    html = (
        '<span class="mq-metric__label">Total spend <span>x</span></span>\n'
        '<span class="mq-metric__value">1,203.50<span class="mq-metric__unit">MXN'
        '<span class="mq-metric__label">Purchase frequency</span>\n'
        '<span class="mq-metric__value">7<span class="mq-metric__unit">purchases'
    )
    assert parse_profile(html) == {"purchases": "7", "spend": "1203.50"}
    assert parse_profile("<p>No sales</p>") == {"purchases": "0", "spend": "0.00"}


def test_the_stock_page_row_is_found_by_store_and_sku() -> None:
    html = (
        "<table><tr><td>Centro</td><td>SKU-1</td><td>Tea</td><td>12 Low</td></tr>"
        "<tr><td>Norte</td><td>SKU-1</td><td>Tea</td><td>4</td></tr></table>"
    )
    assert stock_on_page(html, "Norte", "SKU-1") == 4
    assert stock_on_page(html, "Centro", "SKU-1") == 12
    assert stock_on_page(html, "Sur", "SKU-1") is None


def test_the_served_contract_version_is_read() -> None:
    text = "openapi: 3.1.0\ninfo:\n  title: sales\n  version: 1.9.0\npaths: {}\n"
    assert contract_version(text) == "1.9.0"
    assert contract_version("not a contract") is None


def test_the_f18_04_section_and_junit_are_counted(tmp_path) -> None:
    section = (
        "## Local stack\n\n| Area | Case | Expected | Actual | |\n"
        "|---|---|---|---|---|\n"
        "| a | x | 403 | 403 | ✓ |\n| a | y | 403 | 200 | ✗ |\n"
    )
    assert evidence_counts(section) == (2, 1)
    junit = tmp_path / "j.xml"
    junit.write_text(
        '<testsuites><testsuite tests="5" failures="1" errors="0" skipped="2"/>'
        "</testsuites>"
    )
    assert junit_counts(junit) == {"tests": 5, "failures": 1, "errors": 0, "skipped": 2}


def test_replay_refuses_anything_but_a_local_stack(monkeypatch) -> None:
    monkeypatch.delenv("MOSAIQ_TARGET", raising=False)
    assert is_local("http://127.0.0.1:18080")
    assert not is_local("https://mosaiq.maxthecoder.online")
    assert not is_local("https://127.0.0.1")
    monkeypatch.setenv("MOSAIQ_TARGET", "instance")
    assert not is_local("http://127.0.0.1:18080")


# ---------- F20-02 (#760) ----------


def test_f20_02_reads_elements_whatever_their_prefix() -> None:
    from tests.acceptance.f20_02 import element

    xml = "<c:Error><c:code>forbidden_role</c:code><correlation_id>x</correlation_id>"
    assert element(xml, "code") == "forbidden_role"
    assert element(xml, "correlation_id") == "x"
    assert element(xml, "message") is None


def test_f20_02_reads_the_roles_from_the_committed_contracts() -> None:
    from tests.acceptance.f20_02 import roles

    assert roles("campaigns", "POST", "/v1/campaigns") == ["ADMIN", "MARKETING"]
    assert "STORE_MANAGER" in roles("sales", "GET", "/v1/sales/{sale_id}")


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint is not installed")
def test_f20_02_validates_an_error_against_common_xsd_and_a_body_against_its_own(
    tmp_path,
) -> None:
    from tests.acceptance.f20_02 import xmllint
    from tests.negative.stack import ROOT

    examples = ROOT / "contracts/campaigns/examples"
    valid, detail = xmllint(examples / "forbidden-role.xml", "campaigns")
    assert valid and detail.startswith("contracts/common/v1/common.xsd: ")
    valid, detail = xmllint(examples / "campaign.xml", "campaigns")
    assert valid and detail.startswith("contracts/campaigns/campaigns.xsd: ")
    broken = tmp_path / "broken.xml"
    broken.write_text(
        (examples / "campaign.xml").read_text().replace("DRAFT", "PAUSED", 1)
    )
    assert not xmllint(broken, "campaigns")[0]


def test_f20_02_refuses_a_client_directory_that_holds_a_token_in_any_file(
    tmp_path,
) -> None:
    from tests.acceptance.f20_02 import refuse_tokens

    token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJzdGFmZiJ9.c2lnbmF0dXJl"
    (tmp_path / "bodies").mkdir()
    (tmp_path / "result.json").write_text("{}")
    (tmp_path / "exchanges.json").write_text("[]")
    (tmp_path / "bodies" / "003-staff-response.xml").write_text("<Sale/>")
    refuse_tokens(tmp_path)
    for name in ("result.json", "exchanges.json", "bodies/003-staff-response.xml"):
        path = tmp_path / name
        clean = path.read_text()
        path.write_text(f"{clean}{token}")
        with pytest.raises(SystemExit, match="holds a token"):
            refuse_tokens(tmp_path)
        path.write_text(clean)


# ---------- the rehearsal's preconditions (acceptance/demo.py, #462, #896) ----------

SESSION = "/api/auth/v1/sessions"
STORE, PRODUCT, CAMPAIGN, EXPERIMENT = 11, 2, 301, 31


def example(service: str, name: str, **changes: object) -> dict:
    """A contract's own example, with fields replaced. A replaced field must exist
    in the example, so a renamed field breaks this test and not a rehearsal."""
    body = json.loads((ROOT / "contracts" / service / "examples" / name).read_text())
    for key, value in changes.items():
        assert key in body, f"{service}/{name} has no {key}"
        body[key] = value
    return body


class Gateway:
    """A loopback gateway: answers each (method, path) with one canned reply and
    remembers every request, so a test can say what the collector did."""

    def __init__(self, routes: dict[tuple[str, str], tuple[int, bytes, str]]) -> None:
        self.routes = routes
        self.seen: list[tuple[str, str]] = []
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def handle_all(self) -> None:
                path = self.path.split("?")[0]
                gateway.seen.append((self.command, path))
                length = int(self.headers.get("Content-Length") or 0)
                request_body = self.rfile.read(length) if length else b""
                status, body, kind = gateway.routes.get(
                    (self.command, path),
                    (
                        404,
                        b'{"code":"not_found","message":"no route"}',
                        "application/json",
                    ),
                )
                # The ready-instance fixture has two sessions. Give the marketing
                # sign-in its own opaque token, then make users/me describe that
                # token's principal, as the real gateway does.
                if (
                    self.command == "POST"
                    and path == SESSION
                    and b"marketing@example.com" in request_body
                ):
                    body = json.dumps(
                        {
                            "access_token": MARKETING_TOKEN,
                            "refresh_token": MARKETING_TOKEN,
                            "token_type": "Bearer",
                        }
                    ).encode()
                if (
                    self.command == "GET"
                    and path == "/api/users/v1/users/me"
                    and self.headers.get("Authorization") == f"Bearer {MARKETING_TOKEN}"
                ):
                    marketing = example(
                        "users", "current-user.json", role_code="MARKETING"
                    )
                    body = json.dumps(marketing).encode()
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            do_GET = do_POST = do_DELETE = handle_all

            def log_message(self, *args: object) -> None:
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> Edge:
        self.thread.start()
        return Edge(f"http://127.0.0.1:{self.server.server_address[1]}")

    def __exit__(self, *exc: object) -> None:
        self.server.shutdown()
        self.server.server_close()


def served(body: object, status: int = 200) -> tuple[int, bytes, str]:
    return status, json.dumps(body).encode(), "application/json"


def routes_of_a_ready_instance() -> dict[tuple[str, str], tuple[int, bytes, str]]:
    """What the instance answers when every precondition holds, built from the
    contracts' examples with the ids of this scenario."""
    now = datetime.now(UTC)
    customer = example("customers", "get-customer.json")
    cid = customer["customer_id"]
    user = example("users", "current-user.json", role_code="STORE_MANAGER")
    consent = example("customer-preferences", "get-consent.json")
    for entry in consent["purposes"]:
        if entry["purpose"] in demo.PURPOSES:
            entry["state"] = "GRANTED"
    experiment = example(
        "experimentation",
        "experiment-controlled.json",
        campaign_id=CAMPAIGN,
        state="ASSIGNED",
    )
    experiment["design"]["analysis_at"] = (now + timedelta(days=8)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )
    assignment = example("experimentation", "assignments-page.json")
    assert assignment["assignments"], "the example holds no assignment"
    row = assignment["assignments"][0] | {"customer_id": cid, "arm_kind": "TREATMENT"}
    promotions = example("campaigns", "promotions.json")
    promotion = promotions["promotions"][0] | {
        "campaign_id": CAMPAIGN,
        "is_active": True,
        "store_ids": [STORE],
        "channel_ids": [3],
    }
    routes = {
        ("POST", SESSION): served(
            {"access_token": TOKEN, "refresh_token": TOKEN, "token_type": "Bearer"},
            201,
        ),
        ("DELETE", SESSION + "/current"): (204, b"", "application/json"),
        ("GET", "/api/users/v1/users/me"): served(user),
        ("GET", f"/api/users/v1/store-assignments/{user['user_id']}"): served(
            {"user_id": user["user_id"], "store_ids": [STORE]}
        ),
        ("GET", f"/api/stores/v1/stores/{STORE}"): served(
            example("stores", "store-with-location.json", store_id=STORE)
        ),
        ("GET", f"/api/products/v1/products/{PRODUCT}"): served(
            example("products", "product.json", product_id=PRODUCT)
        ),
        ("GET", f"/api/stock/v1/stock-levels/{STORE}/{PRODUCT}"): served(
            example("stock", "stock-level.json", store_id=STORE, product_id=PRODUCT)
        ),
        ("GET", f"/api/customers/v1/customers/{cid}"): served(customer),
        ("GET", "/api/customers/v1/customers"): served(
            {
                "customers": [customer],
                "page_info": {"page": 1, "size": 100, "total": 1},
            }
        ),
        ("GET", "/api/segments/v1/segment-assignments/current"): served(
            example("segments", "current-assigned.json", customer_id=cid)
        ),
        ("GET", "/api/recommendations/v1/recommendations"): served(
            example(
                "recommendations",
                "recommended.json",
                customer_id=cid,
                usual_store_id=STORE,
            )
        ),
        (
            "GET",
            f"/api/customer-preferences/v1/preference-profiles/{cid}/consent",
        ): served(consent),
        ("GET", "/api/sales/v1/channels"): served(
            example("sales", "list-channels.json")
        ),
        ("GET", f"/api/campaigns/v1/campaigns/{CAMPAIGN}"): served(
            example(
                "campaigns",
                "campaign.json",
                campaign_id=CAMPAIGN,
                status="ACTIVE",
                starts_on=(now.date() - timedelta(days=1)).isoformat(),
                ends_on=(now.date() + timedelta(days=30)).isoformat(),
            )
        ),
        ("GET", "/api/campaigns/v1/promotions"): served(
            {"promotions": [promotion], "page_info": promotions["page_info"]}
        ),
        ("GET", f"/api/experimentation/v1/experiments/{EXPERIMENT}"): served(
            experiment
        ),
        ("GET", "/api/experimentation/v1/experiment-assignments"): served(
            {"assignments": [row], "page_info": assignment["page_info"]}
        ),
        ("GET", "/api/experimentation/v1/experiment-assignments/current"): served(
            example(
                "experimentation",
                "assignment-current-treatment.json",
                customer_id=cid,
                campaign_id=CAMPAIGN,
            )
        ),
    }
    for name in registry_names():
        if implemented(name):
            version = demo.checkout_version(name)
            document = f"openapi: 3.1.0\ninfo:\n  title: {name}\n  version: {version}\n"
            routes[("GET", f"/api/{name}/openapi.yaml")] = (
                200,
                document.encode(),
                "application/yaml",
            )
    return routes


def rehearse(
    tmp_path, routes: dict, monkeypatch: pytest.MonkeyPatch, part_two: bool
) -> tuple[int, Gateway, list[str]]:
    for name in ("FLOOR", "MARKETING"):
        monkeypatch.setenv(f"MOSAIQ_DEMO_{name}_EMAIL", f"{name.lower()}@example.com")
        monkeypatch.setenv(f"MOSAIQ_DEMO_{name}_PASSWORD", "never-in-a-file")
    gateway = Gateway(routes)
    args = argparse.Namespace(
        out=str(tmp_path / "out"),
        customer=example("customers", "get-customer.json")["customer_id"],
        store=STORE,
        product=PRODUCT,
        campaign=CAMPAIGN if part_two else None,
        experiment=EXPERIMENT if part_two else None,
    )
    with gateway as edge:
        code = demo.run(args, edge, pace=0.0)
    record = json.loads((tmp_path / "out" / "record.json").read_text())
    failed = [c["case"] for c in record["checks"] if not c["held"]]
    return code, gateway, failed


def test_demo_part_one_holds_on_a_ready_instance_and_only_reads(
    tmp_path, monkeypatch
) -> None:
    code, gateway, failed = rehearse(
        tmp_path, routes_of_a_ready_instance(), monkeypatch, part_two=False
    )
    assert failed == [] and code == 0
    assert {method for method, _ in gateway.seen} == {"GET", "POST", "DELETE"}
    assert [r for r in gateway.seen if r[0] != "GET"] == [
        ("POST", SESSION),
        ("DELETE", SESSION + "/current"),
    ]
    assert not any("/v1/experiment" in path for _, path in gateway.seen)
    written = "".join(p.read_text() for p in (tmp_path / "out").iterdir())
    assert "P5 customer" in written and "never-in-a-file" not in written
    assert (
        TOKEN not in written and MARKETING_TOKEN not in written and "<masked" in written
    )


def test_demo_part_two_reads_the_campaign_the_experiment_and_the_phones_arm(
    tmp_path, monkeypatch
) -> None:
    code, gateway, failed = rehearse(
        tmp_path, routes_of_a_ready_instance(), monkeypatch, part_two=True
    )
    assert failed == [] and code == 0
    sessions = [r for r in gateway.seen if r[1].startswith(SESSION)]
    assert (
        sessions
        == [("POST", SESSION), ("POST", SESSION)]
        + [("DELETE", SESSION + "/current")] * 2
    )
    paths = {path for _, path in gateway.seen}
    assert gateway.seen.count(("GET", "/api/users/v1/users/me")) == 2
    assert "/api/experimentation/v1/experiment-assignments/current" in paths
    assert f"/api/experimentation/v1/experiments/{EXPERIMENT}" in paths


def test_demo_names_each_precondition_that_does_not_hold(tmp_path, monkeypatch) -> None:
    routes = routes_of_a_ready_instance()
    cid = example("customers", "get-customer.json")["customer_id"]
    routes[("GET", f"/api/stores/v1/stores/{STORE}")] = served(
        example("stores", "store-without-location.json", store_id=STORE)
    )
    routes[("GET", f"/api/stock/v1/stock-levels/{STORE}/{PRODUCT}")] = served(
        example(
            "stock",
            "stock-level.json",
            store_id=STORE,
            product_id=PRODUCT,
            quantity_on_hand=0,
        )
    )
    routes[("GET", "/api/segments/v1/segment-assignments/current")] = served(
        example("segments", "current-no-open-assignment.json", customer_id=cid)
    )
    routes[("GET", "/api/recommendations/v1/recommendations")] = served(
        example("recommendations", "none-match.json", customer_id=cid)
    )
    routes[("GET", "/api/experimentation/v1/experiment-assignments/current")] = (
        404,
        json.dumps(example("experimentation", "assignment-not-found.json")).encode(),
        "application/json",
    )
    routes[("GET", "/api/campaigns/v1/promotions")] = served(
        {"promotions": [], "page_info": {"page": 1, "size": 100, "total": 0}}
    )
    first = next(name for name in registry_names() if implemented(name))
    routes[("GET", f"/api/{first}/openapi.yaml")] = (
        200,
        b"openapi: 3.1.0\ninfo:\n  title: x\n  version: 0.0.1\n",
        "application/yaml",
    )
    code, _, failed = rehearse(tmp_path, routes, monkeypatch, part_two=True)
    assert code == 1
    assert sorted(failed) == sorted(
        [
            f"{first}: the served contract is this checkout's",
            f"store {STORE} has the location the check-in is judged against",
            f"store {STORE} holds at least one unit of product {PRODUCT}",
            "the current assignment state is assigned (#462 step 4)",
            "the recommendations status is recommended (#462 step 4)",
            f"the customer's usual store is the demonstration store ({STORE})",
            f"the floor account reads an active promotion of campaign {CAMPAIGN} "
            f"for store {STORE}",
            "the phone's read answers the treatment arm (F25-17)",
        ]
    )


def test_demo_fails_a_customer_in_control_and_an_analysed_experiment(
    tmp_path, monkeypatch
) -> None:
    routes = routes_of_a_ready_instance()
    cid = example("customers", "get-customer.json")["customer_id"]
    page = example("experimentation", "assignments-page.json")
    page["assignments"] = [
        page["assignments"][0] | {"customer_id": cid, "arm_kind": "CONTROL"}
    ]
    routes[("GET", "/api/experimentation/v1/experiment-assignments")] = served(page)
    analysed = example(
        "experimentation",
        "experiment-controlled.json",
        campaign_id=CAMPAIGN,
        state="ANALYZED",
    )
    routes[("GET", f"/api/experimentation/v1/experiments/{EXPERIMENT}")] = served(
        analysed
    )
    _, _, failed = rehearse(tmp_path, routes, monkeypatch, part_two=True)
    assert sorted(failed) == [
        "the demonstration customer is assigned once, to a treatment arm",
        "the experiment is ASSIGNED and its analysis instant has not come",
    ]


def test_demo_takes_the_campaign_and_the_experiment_together(
    tmp_path, monkeypatch
) -> None:
    args = argparse.Namespace(
        out=str(tmp_path),
        customer="x",
        store=1,
        product=1,
        campaign=1,
        experiment=None,
    )
    with pytest.raises(SystemExit, match="go together"):
        demo.run(args, Edge("http://127.0.0.1:9"), pace=0.0)


def test_demo_judges_each_answer_by_the_fields_the_contracts_name() -> None:
    located = example("stores", "store-with-location.json")
    assert demo.store_location(located) == (
        "latitude 19.432608, longitude -99.133209, geofence_radius_m 150"
    )
    assert demo.store_location(example("stores", "store-without-location.json")) is None
    assert demo.store_location(located | {"location": {"latitude": 1.0}}) is None
    consent = example("customer-preferences", "get-consent.json")
    assert demo.granted(consent, "PERSONALIZATION")
    assert not demo.granted(consent, "MARKETING_CONTACT")
    assert demo.in_store_channel(example("sales", "list-channels.json")) == 3
    assert demo.in_store_channel({"channels": []}) is None

    today = date(2026, 11, 23)
    campaign = example("campaigns", "campaign.json")
    running = campaign | {
        "status": "ACTIVE",
        "starts_on": "2026-11-23",
        "ends_on": "2026-11-23",
    }
    assert demo.campaign_is_running(running, today)
    assert not demo.campaign_is_running(running | {"ends_on": "2026-11-22"}, today)
    assert not demo.campaign_is_running(running | {"status": "DRAFT"}, today)
    assert not demo.campaign_is_running({}, today)

    now = datetime(2026, 11, 23, 12, tzinfo=UTC)
    planned = example("experimentation", "experiment-controlled.json")
    assigned = planned | {
        "state": "ASSIGNED",
        "design": planned["design"] | {"analysis_at": "2026-11-24T00:00:00Z"},
    }
    assert demo.not_analysed_yet(assigned, now)
    assert not demo.not_analysed_yet(assigned | {"state": "ANALYZED"}, now)
    assert not demo.not_analysed_yet(
        assigned | {"design": assigned["design"] | {"analysis_at": None}}, now
    )
    assert not demo.not_analysed_yet(
        assigned
        | {"design": assigned["design"] | {"analysis_at": "2026-11-23T11:59:59Z"}},
        now,
    )

    page = example("experimentation", "assignments-page.json")
    who = page["assignments"][0]["customer_id"]
    assert demo.arms_of(page, who) == [page["assignments"][0]["arm_kind"]]
    assert demo.arms_of(page, "someone-else") == []

    promotions = example("campaigns", "promotions.json")["promotions"]
    first = promotions[0]
    page = {"promotions": [first | {"is_active": False}, first]}
    store, channel = first["store_ids"][0], first["channel_ids"][0]
    assert demo.promotions_for(page, first["campaign_id"], store, channel) == [first]
    assert demo.promotions_for(page, first["campaign_id"] + 1, store, channel) == []
    assert demo.promotions_for(page, first["campaign_id"], store + 99, channel) == []


# ---------- F24-08 (#831) ----------


def test_f24_08_reads_the_bound_from_the_stock_stream_contract() -> None:
    p95, maximum = f24_08.stream_bound()
    assert 0 < p95 <= maximum
    assert f24_08.STREAM_CONTRACT.name == "stock.changed.v1.md"


def test_f24_08_a_contract_without_the_group_stops_the_run(tmp_path) -> None:
    contract = tmp_path / "stock.changed.v1.md"
    contract.write_text(
        "## Propagation bound\n\n| Group | p95 (ms) | Max (ms) |\n|---|---:|---:|\n"
        "| `products` | 3000 | 5000 |\n"
    )
    with pytest.raises(SystemExit):
        f24_08.stream_bound(contract)


def test_f24_08_an_elsewhere_entry_is_not_offered_at_the_store() -> None:
    body = example("recommendations", "v2/recommended-with-alternatives.json")
    assert f24_08.offered_here(body, 120)  # primary
    assert f24_08.offered_here(body, 246)  # an alternative, sold here
    assert not f24_08.offered_here(body, 88)  # held at store 3 only
    assert f24_08.elsewhere(body, 88)["unavailable_reason"] == "out_of_stock"
    assert f24_08.primary(body, 246) is None
    assert not f24_08.offered_here(body, 999)
    assert f24_08.exclusion_count(body, "out_of_stock") == 2
    assert f24_08.exclusion_count(body, "PERSONALIZATION_WITHDRAWN") == 0
    line = f24_08.describe(body, 88)
    assert "elsewhere" in line and "store 3" in line and "out_of_stock 2" in line


def test_f24_08_the_wait_reports_the_first_read_that_holds() -> None:
    now = [0.0]
    answers = iter([{"decision_id": "a"}, None, {"decision_id": "b", "ok": True}])

    def read():
        now[0] += 0.05
        return next(answers), int(now[0] * 100)

    def sleep(seconds: float) -> None:
        now[0] += seconds

    wait = f24_08.wait_for(
        read,
        lambda body: body.get("ok", False),
        0.0,
        5.0,
        0.1,
        clock=lambda: now[0],
        sleep=sleep,
    )
    assert [p.held for p in wait.polls] == [False, False, True]
    assert wait.first_held.decision_id == "b"
    assert wait.last_before is wait.polls[1]
    assert wait.as_dict()["delay_ms"] == pytest.approx(250.0)


def test_f24_08_the_wait_gives_up_after_the_deadline() -> None:
    now = [0.0]

    def read():
        now[0] += 0.4
        return {"decision_id": "a"}, 1

    wait = f24_08.wait_for(
        read,
        lambda _body: False,
        0.0,
        1.0,
        0.1,
        clock=lambda: now[0],
        sleep=lambda s: None,
    )
    assert wait.first_held is None
    assert wait.as_dict()["delay_ms"] is None
    assert len(wait.polls) == 3


def test_f24_08_shape_ignores_what_a_decision_does_not_keep():
    answer = example("recommendations", "v2/recommended-with-alternatives.json")
    stored = {
        "items": [
            {
                "position": i["position"],
                "product_id": i["product_id"],
                "kind": i["kind"],
                "category_id": i["category_id"],
                "availability": {
                    "quantity_on_hand": i["availability"]["quantity_on_hand"]
                },
                "alternative_for": i["alternative_for"],
                "unavailable_reason": i["unavailable_reason"],
            }
            for i in reversed(answer["items"])
        ],
        "exclusions": [dict(e) for e in reversed(answer["exclusions"])],
    }
    assert f24_08.shape(stored) == f24_08.shape(answer)
    stored["exclusions"][0]["count"] += 1
    assert f24_08.shape(stored) != f24_08.shape(answer)


def test_a_run_with_no_checks_does_not_hold(tmp_path) -> None:
    rec = Recorder(tmp_path, "t", "t")
    assert rec.write() is False
    rec.check("c", "a case", 1, 1)
    assert rec.write() is True


def test_f24_08_says_how_to_restore_a_product_left_at_zero() -> None:
    notice = f24_08.left_at_zero_notice(3, 2, 10)
    assert "product 2" in notice and "store 3" in notice
    assert '"quantity": 10' in notice and "RECEIPT" in notice
