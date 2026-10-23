"""Every protected operation refuses a role outside its x-mosaiq-roles (#459).

For each operation of each registered service's contract, the token is a real
one, of the first role the operation does not list (for example
INVENTORY_PLANNER on GET /v1/sales, CUSTOMER on GET /v1/products). An
operation every role may call has no such role and no case.
"""

from __future__ import annotations

import pytest

from tests.negative.stack import Evidence, Service, Stack, registry_names


@pytest.mark.parametrize("name", registry_names())
def test_a_role_outside_each_operation_is_forbidden_role(
    deployed_stack: Stack, services: dict[str, Service], evidence: Evidence, name: str
) -> None:
    service = services[name]
    held = True
    for op in service.protected():
        role = op.outside_role
        if role not in deployed_stack.accounts:
            continue
        reply = deployed_stack.edge.call(
            op.method, service.prefix + op.url(), token=deployed_stack.token(role)
        )
        held &= evidence.check(
            name, f"{role} on {op.method} {op.path}", reply, 403, "forbidden_role"
        )
    assert held, [r for r in evidence.rows if r.area == name and not r.held]
