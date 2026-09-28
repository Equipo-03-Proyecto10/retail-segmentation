# ADR-0023 — Store managers hold `segment.read`, and so read customers and segments

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #280
**Supersedes:** ADR-0010, in part — only its consequence that `segment.read` keeps `STORE_MANAGER` out of the customer directory
**Superseded by:** —

---

## Context

F10-02 (#219, merged in #279) is written *as a store manager*: "I want to see a
customer's recommendations with the reason for each, so that staff can act on them at
the counter." The page is gated on `segment.read`, as the F4-07 permission map assigns
every Phase 10 surface, and `STORE_MANAGER` did not hold it. So the one role the story
names got a 403. The author left this open for the Proxy PO, and it is the same
question the consumption profile (F8-04) raised earlier.

ADR-0010 put customers behind `segment.read`, and said that doing so keeps
`STORE_MANAGER` and `INVENTORY_PLANNER` out of the customer directory. That was a
consequence rather than a requirement: in the first delivery no store-manager story
needed a customer. Delivery 2 has one.

What is uncertain is scope. `segment.read` is one permission for every segment and
customer surface, so a store manager who can see recommendations can also see the
customer directory, profiles, segments, run history, migrations and the model
comparison. Narrowing that to *their* store's customers is not buildable today:
`app_user` has no store, as ADR-0010 already found.

## Decision

`STORE_MANAGER` holds `segment.read` alongside `catalog.read` and `report.read`
(`web/middleware/authz.py`). A store manager therefore reaches every surface gated on
`segment.read`: the customer list and detail, the consumption profile, recommendations,
segments, run history, the migration matrix and explanation, the model comparison and,
when they exist, the Phase 12 segment dashboards. Nothing the role may write changes:
it holds no `segment.write`, `campaign.read`, `campaign.write` or
`segment_run.execute`. `INVENTORY_PLANNER` stays out of the customer directory. The
rest of ADR-0010 stands: customers and segments are gated on `segment.read`, products
and stock on `catalog.read`, in one read-only blueprint.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| A new `recommendation.read` held by `STORE_MANAGER`, gating only the recommendations page | A recommendation names the customer and links their detail and profile, which stay behind `segment.read`, so the page would lead to refusals. Acting on it at the counter needs the customer too. It also adds a permission for one page |
| Scope the store manager to their own store's customers | `app_user` has no store. Adding one is a schema change, plus a query filter on every customer read, well outside a permission change |
| Leave the role without it and rewrite F10-02 for another role | The story names a store manager because that is who is at the counter. Rewriting it changes the requirement to fit the permission |

## Consequences

**What this makes easy.** A store manager opens a customer's recommendations, as
F10-02 intends, and the customer and profile they link to. No new permission, no
schema change, and the default-deny gate is unchanged.

**What this makes hard.** The full customer directory, with contact details, is
visible to every store manager, for every store. If the business later needs
per-store confinement, `app_user` gains a store and this record is superseded. The
menu for this role now shows Segments, Run history, Migration matrix and Model
comparison, which a store manager may not need.

**What must now be true elsewhere.**
- The matrix in [`requirements.md`](../requirements.md) §3 shows `read` under
  *Segments and rules* for `STORE_MANAGER`.
- [`analytics-permission-map.md`](../analytics-permission-map.md) lists the role on
  every `segment.read` row.
- The demonstration guide's role table says the same.

## Compliance

- `tests/test_authz.py` pins the role's exact set as `catalog.read`, `segment.read`
  and `report.read`, with no segment, campaign or run write, and pins its menu.
- Each route test for a `segment.read` surface lists `STORE_MANAGER` among the roles
  served. `INVENTORY_PLANNER` and `CUSTOMER` remain among the refused.
- `tests/test_catalog.py` keeps asserting that a catalog-only role
  (`INVENTORY_PLANNER`) sees no customer or segment link.
