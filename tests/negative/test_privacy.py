"""Every role refused every operation on customer data it may not reach (F29-06).

`test_roles.py` calls each operation with one role outside its
`x-mosaiq-roles`, the first in `ROLE_CODES`. The privacy acceptance test
(#891, "Privacidad") asks more: every role that may not read a customer's
profile or data is refused, on every path. So for the services that hold or
answer customer data, each protected operation is called with *every* role
outside its list, and each one that answers XML is called again with
`Accept: application/xml`. Each call must be 403 `forbidden_role`.

The services are the ones whose registered rule or resources are about a
customer: identity and loyalty cards, preferences and consent, purchases,
segments, R/F/M profiles, recommendations, field engagement and visits,
experiment assignments and attributions, the audit trail that records changes
to them, and KPIs computed from them. A service added to that set needs a line
here; one left out is still covered by `test_roles.py`.

A token's role is checked before any rate limit, body or row
(`mosaiq_kit`), so no case reaches data or spends a caller's request budget.
"""

from __future__ import annotations

import pytest

from tests.negative.stack import Evidence, Service, Stack, registry_names
from tests.negative.test_store_scope import XML, outcome, xml_code

PRIVACY_SERVICES = (
    "customers",
    "customer-preferences",
    "sales",
    "segments",
    "rfm-engine",
    "recommendations",
    "engagement",
    "check-ins",
    "experimentation",
    "audit-trail",
    "analytics-kpis",
)


@pytest.mark.parametrize("name", PRIVACY_SERVICES)
def test_every_role_outside_a_customer_data_operation_is_forbidden_role(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence, name: str
) -> None:
    service = services[name]
    area = f"privacy: {name}"
    held = True
    for op in service.protected():
        url = service.prefix + op.url()
        outside = [r for r in deployed_stack.accounts if r not in op.roles]
        for role in outside:
            token = deployed_stack.token(role)
            reply = deployed_stack.edge.call(op.method, url, token=token)
            held &= evidence.check(
                area, f"{role} on {op.method} {op.path}", reply, 403, "forbidden_role"
            )
            if op.method == "GET" and op.xml_reply:
                reply = deployed_stack.edge.call(
                    op.method, url, token=token, accept=XML
                )
                held &= evidence.record(
                    area,
                    f"{role} on {op.method} {op.path}, in XML",
                    "403 forbidden_role",
                    outcome(reply),
                    reply.status == 403 and xml_code(reply) == "forbidden_role",
                )
    assert held, [r for r in evidence.rows if r.area == area and not r.held]


def test_the_privacy_services_are_registered() -> None:
    """A renamed service must not silently drop out of the privacy sweep."""
    assert set(PRIVACY_SERVICES) <= set(registry_names())
