# recommendations contract

recommendations-service publishes two majors side by side (ADR-0035). A
recommendation item carries ids and figures only. It has no product name, SKU,
price, store name or category name: fetch names with `GET /v1/products?ids=` and
from the categories, stores and sales contracts. The service does not compose
them.

## `/v1`: frozen

`GET /v1/recommendations?customer_id=&limit=` is exactly what 1.0.0 published,
with `recommendations.xsd` (namespace `.../xml/recommendations/v1`). It judges
availability at the customer's usual store, and `quantity_on_hand` is the stock
held there when the result was computed (E-06). A cached result may be up to 120 s
behind; the exact bound is in `info.description` of `openapi.yaml`. When `status`
is not `recommended`, `items` is empty and `status` says why.

Android consumes `/v1` in JSON and Electron in XML, and neither has to change:
the request, the response and the XSD are unchanged, so the oasdiff check and the
XML check against the base XSD both pass. `/v1` never carries an alternative, an
`elsewhere` entry or a decision id, because an old client would show any of them
as stock in this store.

## `/v2`: the reference store and channel, alternatives, the explanation

ADR-0035 makes an element added to an XML response type a breaking change, since
response types are closed and an old-XSD client would refuse it. The new
behaviour therefore ships as `/v2`, with `recommendations.v2.xsd` (namespace
`.../xml/recommendations/v2`, served at `/schema.v2.xsd`) and `info.version`
2.0.0. Its examples are in `examples/v2/`; the error examples are shared in
`examples/`.

| Operation | Purpose |
|---|---|
| `GET /v2/recommendations?customer_id=&limit=&store_id=&channel_id=` | Recommendations judged at a reference store and optional channel, each item with its factors, availability and reasons, with marked alternatives, and a `decision_id` |
| `GET /v2/recommendation-decisions` | Recorded decisions, as summaries, filtered by customer, store, status and time |
| `GET /v2/recommendation-decisions/{decision_id}` | One recorded decision in full: what was offered, what was excluded and why |

The reference store is the `store_id` the caller names, otherwise the customer's
usual store; the reference channel is the `channel_id` the caller names, never
defaulted (ADR-0067). `store_source` says which gave the store.

An item has a `kind`:

| `kind` | Meaning |
|---|---|
| `primary` | A recommendation, available at the reference store and channel, with its `factors` and matched reasons |
| `alternative` | A substitute for an unavailable product, named in `alternative_for`, with the `unavailable_reason` of that product and the single reason `relation_substitute`. It is itself available at the reference store and channel |
| `elsewhere` | The unavailable product itself, held by another store named in `availability.store_id`. It is never a recommendation for the reference store |

`availability` is the service's own judgement at one store and channel, with the
quantity it used. It is not stock-service's `availabilities` resource, which
stays with stock and is read from it by id. `exclusions` counts the products
that matched a signal and were excluded, per reason, and never lists them.

Every `/v2` answer carries the `decision_id` of the decision recorded for it.
`recommendation-decisions` is a top-level resource of this service. A decision
holds identifiers and figures only, and its customer id is removed when the
customer is anonymized.

An operation published ahead of its implementation declares
`x-mosaiq-status: planned` (ADR-0073). Every `/v2` operation is planned until
F24-04 to F24-07 implement it, except `GET /schema.v2.xsd`, which the kit
already serves.
