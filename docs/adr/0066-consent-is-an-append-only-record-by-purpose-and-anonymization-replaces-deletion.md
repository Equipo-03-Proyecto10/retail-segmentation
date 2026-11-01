# ADR-0066 — Consent is an append-only record by purpose, an objection narrows what is done with a customer, and anonymization replaces deletion

**Status:** Proposed
**Owner:** Maximiliano
**Issue:** #793
**Supersedes:** —
**Superseded by:** —

---

## Context

Nothing in the repository records what a customer allowed. `sql/01_schema.sql` has no
consent table, no restriction and no retention rule, and `docs/requirements.md` has no
privacy requirement. What exists is this:

- ADR-0059 folds the privacy-consent capability (no. 25) into customer-preferences
  ("consent and restrictions change only as recorded facts") and customers ("it
  anonymizes and never deletes"), and calls that the weakest fold, because consent
  is append-only while preference sets are replaced whole (RN-54). If a reviewer
  rejects the fold, consent becomes its own Rust service on 5025.
- RN-08 (`transaction_customer_id_fkey ... ON DELETE RESTRICT`) stops a customer with
  sales being deleted, and `contracts/registry.yaml` says a customer is "never deleted
  through services". customers-service holds no `DELETE` grant.
- Deleting is not only blocked for customers with sales. `customer_preferred_channel`,
  `customer_interest_category` and `customer_segment_history` reference `customer` with
  `ON DELETE CASCADE`, and `experiment_assignment` and `transaction` with
  `ON DELETE RESTRICT`. A `DELETE` would erase or refuse exactly the history that R,
  F and M, the segment migrations and the experiment results stand on.
- `fn_audit()` copies the whole `customer` row, name, email and phone included, into
  `audit_log.data_before` and `data_after` for every change (`trg_audit_customer`).
  Only `password_hash` is stripped. ADR-0024 revoked `UPDATE` and `DELETE` on
  `audit_log` from `retail_app`, and says plainly that removing entries "needs the
  owner role, deliberately". `docs/data-model.md` §5 says audit rows are kept
  indefinitely. So anonymizing the `customer` row alone would leave the identity in
  the trail.
- ADR-0060 (accepted) already fixes who may read what of a customer: a "Preferred
  channels, consent record" row (`LOYALTY_OFFICER` reads, `STORE_MANAGER` reads, the
  others do not) and an "Interest categories, restrictions" row, and gives
  `LOYALTY_OFFICER` the `PUT` of preferences and consent. It does not say what the
  record contains or what a refusal changes.
- Services meet only through `api_<suffix>.*_v1` views and Redis Streams relayed from
  the owner's outbox (ADR-0044), and the web reads and writes PostgreSQL directly and
  consumes no service (AGENTS.md). Any rule below has to reach both.

Three parts are judgement calls and the human reviewer should look at them first: the
default for customers already registered (section 1), the 36-month retention period,
and the redaction of the audit trail, which touches the one table ADR-0024 protects.
The record does not choose a legal basis; it was written without legal advice.

## Decision

Consent is recorded per customer and per purpose, as an append-only log owned by
customer-preferences, in a service schema (ADR-0046). The purposes are exactly
`PERSONALIZATION`, `MARKETING_CONTACT` and `PROFILING`. An objection to
personalization is a withdrawal of `PERSONALIZATION`: the segment is still computed,
but nothing is tailored to the customer, no campaign targets them and no experiment
assigns them. Every boundary reads the current state through one published view, and
fails closed when it cannot. Retention is a rule of customers-service: a customer row
is never deleted, and after a request or 36 months with no sale it is anonymized in
place, so that the key, the sales, the R, F and M and the audit entries stay and the
identity goes. The sections below make this exact.

### 1. Purposes and the consent record

| Purpose | What it permits | Not covered |
|---|---|---|
| `PROFILING` | Deriving facts about the individual from their behaviour: segment assignment (RFM rules and K-means), inferred interests (F21-18), and including the customer in a model's input | Counting a customer in aggregates that name no one |
| `PERSONALIZATION` | Using a profile to tailor what the customer sees or is offered: recommendations, segment- or interest-based campaign audiences, experiment assignment | Prices and promotions applied to every buyer alike (F25-05) |
| `MARKETING_CONTACT` | Sending the customer a campaign communication | Transactional messages about a sale |

One row is appended per decision, never updated or deleted. The shape the contract
(#794) has to carry, as a proposal and not as DDL:

| Field | Meaning |
|---|---|
| `customer_id`, `purpose` | The customer and one of the three purposes |
| `decision` | `GRANTED` or `WITHDRAWN` |
| `recorded_at` | Server time, never client-supplied |
| `recorded_by` | The `app_user` who took it, taken from the verified token, never from the body |
| `source` | `STAFF_ON_BEHALF`, `CUSTOMER_SELF` or `MIGRATION` |
| `basis` | `EXPLICIT` (the customer said so) or `PROGRAMME_DEFAULT` (the backfill below) |

The current state of a purpose is its latest row. A withdrawal never rewrites a grant;
both stay, which is what makes the log evidence. Append-only is enforced twice, as
ADR-0050 does for conversions: `svc_preferences` holds `SELECT` and `INSERT` on the
table and nothing else, and the service exposes no route that edits or removes a row.
A **restriction** is a narrower fact inside a granted purpose, recorded the same way:
a channel the customer does not want to be reached on, or an interest category they
do not want offered (`kind`, `target_id`, `IMPOSED` or `LIFTED`). The existing
whole-set `PUT`s of RN-54 are unchanged.

**Default for customers already registered.** No consent exists today, so the
migration appends one row per existing customer and purpose, with `source =
MIGRATION`, `recorded_by` null (the actor is genuinely unknown, as for the seed in
`fn_audit`) and `basis = PROGRAMME_DEFAULT`:

- `PROFILING` and `PERSONALIZATION`: `GRANTED`. Segmentation is the platform's
  purpose, and the issue's wording treats personalization as something a customer
  objects to, which is an opt-out.
- `MARKETING_CONTACT`: `WITHDRAWN`. Contact is the most intrusive use, so it is
  opt-in.

A customer registered afterwards has no default: registration records the three
decisions explicitly (`basis = EXPLICIT`), and a purpose with no row is not granted. A
`PROGRAMME_DEFAULT` row is reported as such and never as the customer's consent.

### 2. What an objection changes

The state is read from `api_preferences.consent_state_v1` (one row per customer and
purpose) and `api_preferences.restriction_v1` (current restrictions). Both are
versioned views under ADR-0044, with a `contracts/db/` document, granted `SELECT` to
each reader. A change is announced on `stream:preferences.v1.consent_changed`, keys
only, relayed from the owner's outbox like `stream:preferences.v1.changed`; a consumer
re-reads the view and never trusts the event for state. If a reader cannot read the
view, it treats the purpose as withdrawn and says so.

| Boundary | `PROFILING` withdrawn | `PERSONALIZATION` withdrawn | `MARKETING_CONTACT` withdrawn |
|---|---|---|---|
| Segmentation (segments, and the web's batch run) | The customer's open assignment is closed and they are reported as unassigned with reason `PROFILING_WITHDRAWN`, as RN-21 reports unassigned states as they are. They are skipped by incremental updates (ADR-0063) and by later training sets. Already-published model versions stay immutable (ADR-0064) | No change. The segment is still computed and counted in aggregates, because it describes past sales, not an offer | No change |
| Recommendations (RN-40) | No change beyond what the segment loss implies | No recommendation is tailored to the customer. The answer is "nothing qualifies" with reason `PERSONALIZATION_WITHDRAWN` (RN-40 already requires a reason). A restricted category never appears for a customer who has not withdrawn | No change |
| Campaign eligibility | Excluded from segment-based audiences, since they have none | Excluded from every audience built on segment or interest | Excluded from every audience, whatever its criterion. A restricted channel narrows the contact channel |
| Experiments | Not assigned to a new experiment | Not assigned to a new experiment, and no longer exposed to a running one. Existing assignments and conversions stay: they are append-only facts (ADR-0050), and removing a customer after assignment would bias the arm comparison. Results report them as withdrawn | No change |
| Inferred interests (F21-18) | Deleted and no longer inferred. Declared interests are the customer's own and stay | Not used for tailoring | No change |

Three consequences are easy to miss. R, F and M of past runs stay in
`customer_segment_history`: they are facts about sales and a closed run is not
rewritten. The permitted profile (ADR-0060) shows the restrictions and the consent
state, so staff see the objection at the till. And a withdrawal takes effect at the
next read, because every consumer re-reads the view; a cache that holds a profile
(recommendations' cache is invalidated by stream today) is invalidated by the consent
event as well.

The web is not a consumer of services, but the rule must not depend on that: its
batch segmentation reads the same view through a `SELECT` grant to `retail_app`, which
is the "carried by the database" route of ADR-0045.

### 3. Retention and anonymization

**A customer is never deleted.** RN-08 stays as written. Retention ends in
anonymization, a rule of customers-service (ADR-0059): an in-place `UPDATE` of the
identifying columns, executed by an explicit operation and idempotent.

| Kept | Why | Removed or replaced | How |
|---|---|---|---|
| `customer_id` (a random UUID) | Every foreign key, and the join that gives R, F and M | `name` | Replaced by a fixed placeholder, because the column is `NOT NULL` |
| `registered_on`, `registration_channel_id` | RN-37 (no sale before registration) and the channel reports; neither identifies anyone | `email`, `phone` | Set to `NULL`; `customer_email_check` already admits it |
| `transaction`, `transaction_line` and reversals | Sales history is the basis of every segment | `user_id` | Set to `NULL`. The login account is deactivated by users-service on the event below |
| `customer_segment_history`, `segmentation_run` | R, F and M and the migrations are measured on them | Declared preferences and inferred interests | customer-preferences empties both sets on the event below; the consent log is kept |
| `experiment_assignment`, conversions | Results stay reproducible | Loyalty identifiers (F21-13) | Revoked |
| Consent and restriction rows | They prove what was allowed; they hold a UUID and an actor, no identity | | |
| Engagement events in MongoDB | They key on the UUID alone | | |

Anonymization is irreversible, sets `anonymized_at` on the row, and from then on the
customer is read as anonymized: `PATCH` returns 409, a new sale is refused, and
`api_customers.customer_ref_v1` exposes the flag so that sales and the others can
check it. customers-service announces `customer.anonymized` on its own outbox stream,
which customer-preferences and users-service consume; nobody calls anybody over HTTP.

Two triggers, both executed by `ADMIN`:

- **A request** from the customer, passed on by `LOYALTY_OFFICER`.
- **Expiry**: no sale and no check-in for 36 months. This number is a judgement call
  with no source in the repository. A job lists the candidates; an administrator
  confirms, so no unattended process rewrites identities.

**The audit trail.** Audit entries are evidence and are not deleted. Their identifying
payload is not kept. The anonymization step, as part of the operator-applied section of
the schema (ADR-0040) and run as the schema owner, which is the exception ADR-0024
foresaw, replaces `name`, `email` and `phone` inside the `customer` entries of that
customer with a redaction marker. Entity, key, action, timestamp and actor are
untouched, and the redaction itself is recorded as a domain event (F21-24) naming the
administrator. No `svc_*` role gains `UPDATE` on `audit_log`. After anonymization the
same redaction applies to that customer's later entries through `fn_audit`.

### 4. Authorized history

A customer's history is four things: their sales, their segment history, their
consent and restriction log with the preference sets, and their visits and engagement.
ADR-0060's table decides who sees which field; this record adds the history rule.

| History | Who may read it | Source of the rule |
|---|---|---|
| Consent and restriction log | `LOYALTY_OFFICER`, `STORE_MANAGER`, `ADMIN`, `AUDITOR`. Current state and restrictions only, also `SALES_ASSOCIATE` | ADR-0060 table |
| Segment history | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `AUDITOR`, and the floor roles ADR-0060 lists for segments, in its representation | Operation policy, ADR-0060 |
| Sales history of one customer | `ADMIN`, `ANALYST`, `STORE_MANAGER`, `MARKETING`, `AUDITOR`, `SALES_ASSOCIATE`, `COMMERCIAL_SUPERVISOR` (narrowed) | Operation policy, ADR-0060 |
| Anything of an anonymized customer | Same roles; the identity fields are already gone | This record |

`CUSTOMER` reads none of it in this delivery: the policy gives it only sign-in, its
session and `GET /v1/users/me`, and giving it an access route is a new decision.

**Audited reads.** A read of the history of one named customer is recorded as a
domain event with the actor, the customer id, the kind of history and the time, and
never the content, so the trail does not become a second copy of the data. This applies
to the consent log, the segment history and the per-customer sales list, and to every
refusal (403) on those routes. It does not apply to the permitted profile read at the
till, to directory search, or to aggregates. The storage and contract of these events
belong to F21-24 (#802).

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Delete the customer's row on request | RN-08 forbids it for any customer with sales, and the `CASCADE` foreign keys would also erase segment history and preference rows. R, F and M and the experiment results would be unreproducible |
| One consent flag on `customer` | It cannot show who took it or when, a withdrawal erases the grant, and it cannot separate three purposes. The issue asks for an append-only record |
| Opt-in for all three purposes, existing customers included | Every existing customer would drop out of segmentation, recommendations and campaigns at once, and the second delivery's analytics would report almost no assignments. The issue's own wording treats personalization as something one objects to |
| Opt-out for all three, marketing contact included | Contact reaches the person directly; defaulting it to granted for people who never said anything is the weakest position |
| An objection removes the customer from segmentation | Segments are computed from sales and counted in reports (RN-20, RN-21). Hiding the customer from aggregates changes the numbers without protecting them. `PROFILING` exists for the case where the computation itself is refused |
| Consent as its own service (the other branch of ADR-0059) | It would add a container for an append-only table that customer-preferences can own, at 48 MB. Revisit only if a reviewer judges the two invariants cannot share a service |
| Leave the identity in `audit_log` | Anonymization would be cosmetic: `data_before` holds the name, email and phone verbatim |
| Grant a `svc_*` role `UPDATE` on `audit_log` to redact | It reopens the hole ADR-0024 closed, for every service that role reaches |
| Anonymize on a retention timer with no human confirmation | A mistaken rule would rewrite identities with no way back |

## Consequences

**What this makes easy.** Every boundary has one place to ask whether it may use a
customer, and the answer is dated and attributed. A customer can object without any
sales history, R, F or M changing. Anonymization keeps the key, so no foreign key and
no join breaks.

**What this makes hard.** Every reader of a customer now depends on a consent view and
fails closed without it, and the web's batch run has to read it too. Anonymization is
irreversible and rewrites audit payloads, which is the first sanctioned exception to
the append-only trail. An anonymized customer cannot buy again, so a returning person
registers as a new customer. The 36-month period and the defaults are choices that need
an owner. The fold in ADR-0059 now puts an append-only log beside a replace-whole rule
in one service, so customer-preferences is the service most likely to need splitting.
Reads of history must be audited without logging content, and experiment results now
carry a "withdrawn" count.

**What must now be true elsewhere.**
- **#794 (F21-16)** publishes the consent, restriction and preference-history contract,
  JSON and XML, the two `api_preferences` views and the stream, following this record.
- **#795 (F21-17)** implements the log, the grants, the append-only guard and the
  backfill, in `sql/01_schema.sql` with at least 30 seed rows per new table.
- **#797 (F21-19)** adds `anonymized_at`, the anonymization operation, the
  `customer.anonymized` stream, the candidate job and the audit redaction.
- **#800 (F21-22)**, the customer-visit contract, reads consent and cannot record a
  visit against a customer who has withdrawn `PROFILING`.
- **#829 (F24-06)** excludes what consent and restrictions forbid in recommendations,
  **#835 (F25-04)** does so in audiences, and **#811 (F22-15)** adds the unassigned
  reason `PROFILING_WITHDRAWN`.
- **#802 (F21-24)** stores the audited reads and the redaction event.
- **#891 (F29-06)** tests privacy, revocation and disconnection against the table in
  section 2.
- ADR-0060's table and `docs/requirements.md` §3 are not edited; if the reviewer
  changes who reads the consent log, this record changes before acceptance.

## Compliance

- A test that appends a `WITHDRAWN` row and asserts the previous `GRANTED` row is
  unchanged, and that `UPDATE` and `DELETE` on the table fail with
  `insufficient_privilege` as `svc_preferences`.
- `contracts/db/check_grants.sh` and `check_boundaries.sql` prove that only the
  readers named in the view's contract hold `SELECT` and that no other boundary reads
  the base table.
- For each cell of the table in section 2, a test per reader (recommendations,
  segments, campaigns, experimentation, the web's batch run) with the view
  unreadable, which must behave as withdrawn.
- After anonymizing a customer in a fixture: the row counts of `transaction`,
  `customer_segment_history` and `experiment_assignment` are unchanged, R, F and M
  recompute equal, `name`, `email` and `phone` match nothing in `audit_log`, and
  `audit_log` still has the same number of rows.
- The three scripts run clean in order from an empty database, and the backfill
  produces three rows per seeded customer.
