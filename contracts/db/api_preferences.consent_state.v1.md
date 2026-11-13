# `api_preferences.consent_state_v1`

## Contract

| Field | Value |
|---|---|
| Object | `api_preferences.consent_state_v1` |
| Kind | PostgreSQL view |
| Owning boundary | customer-preferences |
| Producer | customer-preferences-service |
| Consumers | recommendations, campaigns, rfm-engine |

The view publishes what each customer currently allows, one row per customer and
purpose: the latest row, by `decision_id`, of the append-only consent log
(ADR-0066). A purpose that was never decided has no row, and a consumer reads the
absence as not granted; `NOT_RECORDED`, which the API reports, is that absence.
The view carries the decision, when it was recorded and on what basis, and not who
took it: no consumer needs the actor, and reading a customer's current state is not
reading the log. The log, and the history of one named customer, are read only
through customer-preferences' own API (`listConsentDecisions`), which records the
read. Consumers name only the columns they read.

## Columns

| Column | PostgreSQL type | Meaning |
|---|---|---|
| `customer_id` | `uuid` | The customer who decided. |
| `purpose` | `character varying(20)` | `PERSONALIZATION`, `MARKETING_CONTACT` or `PROFILING`. |
| `decision` | `character varying(20)` | `GRANTED` or `WITHDRAWN`: the latest decision for that purpose. |
| `decision_id` | `bigint` | The log row that decision is. It rises in the order decisions were recorded, so a larger id is a later decision. |
| `recorded_at` | `timestamp with time zone` | When the decision was recorded, on the server's clock. |
| `basis` | `character varying(20)` | `EXPLICIT` when the customer said so, `PROGRAMME_DEFAULT` for the backfill given to customers registered before consent existed. A `PROGRAMME_DEFAULT` row is reported as such and is never the customer's own consent. |

`(customer_id, purpose)` is unique: a purpose has one current decision. Every row
names an existing customer. A customer anonymized by customers-service keeps its
rows, which hold a UUID and no identity (ADR-0066 section 3).

## What a consumer does with it

A purpose is granted only when its row says `GRANTED`. A missing row, a
`WITHDRAWN` row and a view the consumer cannot read are all not granted: a reader
that cannot read the view treats the purpose as withdrawn and says so, and never
falls back to a cached grant (ADR-0066 section 2). Each effect below is the
decision of that record and is not re-decided here.

| Consumer | Purposes it reads | When the purpose is not granted |
|---|---|---|
| recommendations | `PERSONALIZATION` | The answer is "nothing qualifies" with reason `PERSONALIZATION_WITHDRAWN`, and no segment, history or interest of the customer is read (RN-40, ADR-0067). A cached result is invalidated by `stream:preferences.v1.consent_changed` |
| campaigns | `PERSONALIZATION`, `MARKETING_CONTACT` | Without `PERSONALIZATION` the customer is in no audience built on a segment or an interest; without `MARKETING_CONTACT` they are in no audience whatever its criterion |
| rfm-engine | `PROFILING` | The customer's open assignment is closed and they are reported as unassigned with reason `PROFILING_WITHDRAWN`, and they are skipped by incremental updates (ADR-0063) |

model-registry (training sets), check-ins (customer visits, F21-22) and
experimentation (an experiment's frame) also read this view under ADR-0066. Each is
named here, with its grant, by the contract story of that service, which is an
addition and not a breaking change. An experiment's frame is judged at one instant
`T` (ADR-0068): this view is the current state only, so the state at a past instant
is derived from the log (`getConsentState` with `as_of`), and a reader that needs it
in SQL is given its own versioned object by the story that needs it.

## Writers

Only customer-preferences writes the log, and only by appending: `svc_preferences`
holds `SELECT` and `INSERT` on it and nothing else, and the service has no route
that edits or removes a row. A decision commits with its outbox row in one
transaction, so when `stream:preferences.v1.consent_changed` carries the event the
view already shows the change. The consumers above re-read the view for the
customer and never take consent from the event.

## The web

The web is not a consumer: it cannot read `api_preferences` (ADR-0046) and consumes
no service. Its batch segmentation reads a read-only view in `public` with this
definition, the pattern of `net_sale` in ADR-0062 and the database route of
ADR-0045, and no grant on `api_preferences` goes to `retail_app` (ADR-0066 section
2). That view is `public.consent_state` (F21-17, #795): the same five columns as
this view's, `customer_id`, `purpose`, `decision`, `decision_id`, `recorded_at` and
`basis`, over this view, with `SELECT` for `retail_app` and nothing else.

## Compatibility

Adding a column is compatible because consumers name their selected columns.
Removing or renaming a consumed column, changing its PostgreSQL type, changing the
values of `purpose`, `decision` or `basis`, or changing the one-latest-row-per-purpose
rule is breaking. A breaking revision is published as
`api_preferences.consent_state_v2` with a matching `.v2.md` contract beside v1; v1
remains until recommendations, campaigns and rfm-engine have moved.
