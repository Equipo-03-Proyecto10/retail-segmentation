"""The inventory demand report keeps the rules of ADR-0079 (#881)."""

from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "contracts/analytics-kpis"
OPENAPI = CONTRACT / "openapi.yaml"
EXAMPLES = CONTRACT / "examples"

V1_KPI_CODES = [
    "net-sales",
    "spend",
    "retention",
    "migrations",
    "conversion",
    "recommendation-performance",
    "campaign-performance",
]
PLANNER_ROLES = {"ADMIN", "ANALYST", "INVENTORY_PLANNER", "AUDITOR"}
CUSTOMER_FIELDS = ("customer_id", "customer_count", "customer_ids")


def _spec() -> dict:
    return yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))


def test_demand_operation_is_readable_only_by_the_planner_roles():
    operation = _spec()["paths"]["/v1/kpi-reports/demand"]["get"]
    assert operation["operationId"] == "getDemandReport"
    assert set(operation["x-mosaiq-roles"]) == PLANNER_ROLES


def test_demand_report_has_no_customer_field():
    schemas = _spec()["components"]["schemas"]
    for name in ("DemandReport", "DemandRow"):
        properties = schemas[name]["properties"]
        for field in CUSTOMER_FIELDS:
            assert field not in properties, f"{name} must not carry {field}"


def test_demand_examples_name_no_customer():
    for path in (EXAMPLES / "demand.json", EXAMPLES / "demand.xml"):
        text = path.read_text(encoding="utf-8")
        for field in CUSTOMER_FIELDS:
            assert field not in text, f"{path.name} must not carry {field}"


def test_v1_kpi_catalog_is_unchanged():
    # Adding a value to KpiCode would be breaking within v1 (contracts/README.md),
    # so the demand report has its own response type and the catalog keeps seven KPIs.
    schemas = _spec()["components"]["schemas"]
    assert schemas["KpiCode"]["enum"] == V1_KPI_CODES
    assert schemas["Dimension"]["enum"] == ["segment", "store", "channel", "period"]


def test_demand_dimensions_include_category_only_in_the_demand_type():
    schemas = _spec()["components"]["schemas"]
    assert schemas["DemandDimension"]["enum"] == [
        "segment",
        "store",
        "category",
        "period",
    ]
    assert "category" not in schemas["Dimension"]["enum"]
