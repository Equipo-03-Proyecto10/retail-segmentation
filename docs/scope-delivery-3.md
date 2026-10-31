# Scope — Delivery 3, commercial intelligence platform

This document scopes the third delivery, from the instructor's retrospective on
the second delivery and brief for the next one. The boundaries of the earlier
work stay in [`scope.md`](scope.md),
[`scope-delivery-2-analytics.md`](scope-delivery-2-analytics.md) and
[`scope-delivery-2-distributed.md`](scope-delivery-2-distributed.md); nothing here
edits them. Every statement of status below was checked against the repository on
2026-10-31, not taken from the report the instructor read.

---

## 1. Objective

Turn MOSAIQ into an enterprise commercial intelligence and personalization
platform that analyses purchases and behaviour, updates customer segments,
recommends available products and measures the real impact of marketing
campaigns.

One process must be followed end to end: **a purchase modifies the consumer's
profile; that may cause a segment migration; recommendations update according to
the inventory; a campaign runs, and its effect is evaluated by a properly designed
experiment.**

The instructor is explicit that the main challenge is not more microservices, but
components that work together, with consistent business rules and commercially
verifiable results.

## 2. Binding requirements

| Application | Responsibility | Communication |
|---|---|---|
| Web | Administration, customers, products, segmentation, campaigns, indicators and reports | Server-rendered pages; the APIs are the services' (Q-17) |
| Android | Store operations: customer identification, sales, loyalty, promotions and interaction records | REST, JSON over HTTPS only |
| Desktop | Advanced analysis, model configuration, migrations, A/B experiments and commercial planning | REST, XML validated by XSD only |

Four capabilities matter most:

1. **Incremental update.** Recording a sale must not recalculate every customer. The system identifies which profiles changed and updates only those, and a total verification recalculation stays possible.
2. **Consistent history.** Migrations are kept per model version. A change of RFM limits or of K-means neither rewrites the past nor presents a methodological difference as a change in behaviour.
3. **Personalization bound to inventory.** A recommendation is useful only if the product is available in the relevant channel and store, or a valid alternative is offered, and the service can explain it.
4. **Reliable measurement.** A campaign can raise total sales without having caused the rise: control groups, sound measurement and uncertainty are required.

Every repository rule in [`AGENTS.md`](../AGENTS.md) stays in force: one rule per
service, contracts before code, ADR-0034 to ADR-0046, DDL only in
`sql/01_schema.sql`, 4NF with a written justification, 30 seed rows or an
exemption, exactly one administrator, parameterized SQL, RS256 signed only by
auth-service, and no secrets in source.

## 3. The retrospective against the repository

Two findings describe a state the repository has already moved past. Each row
states what remains.

| ID | Finding | What the repository shows | Status |
|---|---|---|---|
| RT-01 | The gateway does not publish the APIs | **Published since the report.** F18-02 (#452) installed the 15 includes on 2026-10-23, and 15 of 15 readiness routes answered 204 through Cloudflare ([evidence](evidence/f18-02-api-edge.md)). The negative suite held 259 cases on the instance and 18 from outside ([evidence](evidence/f18-04-negative-tests.md)). The team discarded this finding on 2026-10-31. The instructor's acceptance tests still need a real authenticated operation from each client, with its request, response and persisted row (F20-01, F20-02) | Discarded |
| RT-02 | Failures blamed on the environment; two campaign failures uninvestigated | No single run covers every suite for one version: `ci.yml`, `services.yml` and `nightly.yml` set up separately. `services/categories/tests/test_redis.py` turns `appendonly` off on its server. CI and `compose.distributed.yaml` run PostgreSQL 16, the instance 18. Nobody on the team can identify the report, so the two failures are unnamed, and F20-05 works from a fresh run | Open |
| RT-03 | Parity and propagation delay | `tests/golden/` pins four ports (sales, recommendations, users, campaigns) on fixed inputs. There is no parity for RFM, inventory, audit or segments, and none after a change. The ops console reports stream lag in entries, not in time | Open |
| RT-04 | Dynamic against manual segmentation | The only way a segment changes is the web's administrator run (RN-05, E-10). No trigger, schedule, model version or treatment of late or voided sales exists, and sales cannot be cancelled or returned | Open |
| RT-05 | Statistical validation of K-means | Seeded and reproducible (RN-58), k-means++, min-max normalization, inertia, and silhouette up to 2,000 customers. No recorded choice of k, treatment of extreme values or stability study, and the seed holds 30 customers | Partly met |
| RT-06 | Concept drift | Migration matrices and consumption shifts exist. No statistical test of a distribution and no predictive task exist, so nothing today can claim drift | Open |
| RT-07 | Recommendations and inventory | RN-40 excludes inactive and out-of-stock products at the customer's usual store, and `stock.v1.changed` invalidates the cache. No reference store or channel, discontinued status, alternatives or persisted explanation exist, and no test shows a stock change updating a result | Partly met |
| RT-08 | Campaign measurement | The web separates assignment, exposure and conversion and computes an intent-to-treat uplift with a 95% interval, validated by A/A and injected-effect tests. No service exposes it. No audience, eligibility, sample size or balance check exists, a control is optional (ADR-0028), and one sale can convert for two experiments | Partly met |
| RT-09 | Flask and Rust results not published | **Published since the report**: [the F15-02 report](evidence/products-reads-flask-vs-rust.md) (#455) holds 80 runs, reproducible from `bench/results/`. Its gaps are real: a Raspberry Pi 5, the memory limit not enforced, one calibration, RSS only, and no CPU measure | Partly met |
| RT-10 | No full demonstration; no tests with real users | #462 (F18-05) is open, and no usability test has been run | Open |

## 4. The fifteen services against the catalog

The instructor's catalog widens each service. It keeps the registered ports.

| Service | Port | Already there | Missing |
|---|---|---|---|
| auth | 5001 | Sign-in, RS256, rotating refresh, revocation, Redis sessions, attempt limits, temporary lock | Evidence only |
| users | 5002 | Users, roles, audit, deactivation with history | Assigned stores, the new floor roles |
| products | 5003 | SKU, category, price, active flag | Commercial status, attributes, photo in the contract, barcode, relations |
| categories | 5004 | Tree, validations, links to products and interests | Evidence only |
| stores | 5005 | Directory, location | Hours, channels, status writes, commercial rules |
| stock | 5006 | Levels per store, consumption by a sale | Channels, movements, reservations where they apply |
| customers | 5007 | Registration, search, edits | Loyalty identifiers, per-role views, retention, anonymization |
| customer-preferences | 5008 | Declared channels and interests | Inferred interests, history, consent, restrictions |
| sales | 5009 | Sales, lines, idempotency, store, channel, date | Returns, cancellations, valid discounts |
| segments | 5010 | Reads of labels, assignments and history | Model versions, a write path, migrations, quality indicators. RFM and clustering live only in the web |
| recommendations | 5011 | By customer, from segment, history, preferences and stock, with reasons | Reference store and channel, exclusions, persisted explanation, alternatives |
| engagement | 5012 | `QR_SCAN` and `RECO_CLICK` in MongoDB, summaries, voids | Visits, impressions, exposures, carts, navigation |
| check-ins | 5013 | Staff check-ins by GPS | Authorized customer visits and their link to campaigns |
| campaigns | 5014 | Lifecycle and transitions | Audiences, budgets, eligibility, promotions |
| audit-trail | 5015 | Reads of the table-change log | Calculations, models, permissions, critical events, authorized reads |

## 5. Capabilities 16 to 25

The instructor recommends these, says not all need a container, suggests
integrating some in segments, sales, recommendations or campaigns to avoid
artificial fragmentation, and notes that ports 5016 to 5025 are proposals.
**F19-02 decides.** The working assumption below lets the stories be written, and
says where work lives only until that record is accepted.

| # | Capability | Working placement | Why |
|---|---|---|---|
| 16 | rfm-engine | New container, 5016 | A stream-driven worker with its own invariant |
| 17 | segment-migration | Folded into segments | A read over assignment history |
| 18 | drift-monitoring | Folded into model-registry | A version carries its reference distribution |
| 19 | model-registry | New container, 5019 | K-means training stays in Python, while segments is Rust |
| 20 | experimentation | New container, 5020 | Append-only facts distinct from a campaign's lifecycle |
| 21 | conversion-attribution | Folded into experimentation | The same measured facts |
| 22 | analytics-kpis | New container, 5022 | A read-only rule across many boundaries |
| 23 | inventory-personalization | Folded into recommendations | The same rule, RN-40 |
| 24 | data-quality | Folded into sales | An import is a batch of sales, RN-37 |
| 25 | privacy-consent | Folded into customer-preferences and customers | Consent is a preference fact, and anonymization a customer fact |

**The budget binds this choice.** The `mem_limit`s sum to 2,832 MB against the gate
of 3,000 MB, which leaves room for one Flask service at 160 MB. F18-01 measured
Flask services at about 83 MiB, but idle. F19-02 and F19-08 re-baseline it.

## 6. What must change in decided records

These accepted decisions conflict with the brief. Each is superseded in part by
the story that needs it, never edited.

| Record | Conflict | Decided by |
|---|---|---|
| ADR-0041 | `STORE_MANAGER` and `ADMIN` are the only floor roles; the brief has four profiles | F19-03 |
| ADR-0048 | Store managers are not confined to a store; the brief asks for assigned stores and local indicators | F19-03 |
| ADR-0034, ADR-0040, ADR-0043 | Fifteen services under a 3,000 MB gate | F19-02 |
| ADR-0031, RN-14 | Stock changes only through a sale; the brief asks for movements | F21-04 |
| ADR-0039, RN-09 | No reversal of a sale exists | F21-01 |
| RN-05, E-10 | Only the web's administrator run writes segments | F22-01 |
| RN-39 | Runs are compared by label whatever the method | F22-02 |
| ADR-0028 | An experiment may omit a control | F25-01 |
| ADR-0053 | Only conversion is measured | F25-01 |
| RN-40, E-17 | Recommendations use the usual store, and are pinned to the web's rule | F24-02 |

## 7. Phases

Phases 0 to 18 are the earlier deliveries. Story ids and the dependency graph are
in [`backlog.md`](backlog.md). F15-03 joins the existing epic #401.

| Phase | Name | Contents | Epic |
|---|---|---|---|
| 19 | Third-delivery foundation | Scope, the placement of capabilities 16 to 25, the memory budget, the floor roles and store scope | #740 |
| 20 | Verified integration of the deployed system | Authenticated client operations on the instance, one-command verification, parity, propagation delay, a consolidated result | #741 |
| 21 | Completing the fifteen services | Reversals, stock movements and channels, product and store extensions, loyalty, consent, the engagement taxonomy, customer visits, domain audit | #742 |
| 22 | Dynamic segmentation and model versions | rfm-engine, incremental update and its verification, model-registry and versions, segment changes, migrations by version | #743 |
| 23 | Model validity and drift | An evaluation dataset, variables, normalization, k, stability, interpretation, RFM against K-means, data drift and concept drift | #744 |
| 24 | Inventory-bound recommendations | Reference store and channel, availability, exclusions, a recorded explanation, alternatives | #745 |
| 25 | Campaign measurement | Audiences, budgets, promotions, experimentation, stable assignment, sample size, exposure, conversion, attribution, effect with uncertainty | #746 |
| 26 | KPIs, planning and data quality | analytics-kpis, demand against availability, imports through sales, reconciliation | #747 |
| 27 | Android store operations | One experience per profile, loyalty identification, carts, explained recommendations, real exposure, offline drafts, notifications | #748 |
| 28 | Desktop analysis console | The console shell, the fourteen modules, the proof of its security defaults | #749 |
| 29 | Validation, acceptance and demonstration | Hand-computed validations, the sixteen acceptance tests, usability, the release, the complete loop, catalog compliance | #750 |

## 8. Deliverables

| # | Deliverable | Location |
|---|---|---|
| 1 | Accepted decisions | `docs/adr/` |
| 2 | Contracts for every new resource, before its code | `contracts/` |
| 3 | The fifteen services completed, and the services F19-02 adds | `services/` |
| 4 | Schema, seed and 4NF justification | `sql/`, `docs/data-model.md` |
| 5 | Android store operations and the desktop analysis console | `clients/android/`, `clients/desktop/` |
| 6 | Model studies: variables, normalization, k, stability, interpretation, RFM against K-means | `docs/evidence/` |
| 7 | Validation of RFM, K-means, migrations, incremental update, drift, recommendations, A/B testing and attribution | `docs/evidence/` |
| 8 | Flask and Rust comparison under equal conditions | `bench/`, `docs/evidence/` |
| 9 | Consolidated result of one version, from a clean environment | `docs/evidence/` |
| 10 | Usability tests and the demonstration of the complete loop | `docs/evidence/` |
| 11 | Service catalog compliance, and the updated delivery documents | `docs/` |

## 9. Out of scope

- **Kubernetes, managed databases and any second VM** (C-5, C-6).
- **Any Redis connection or service call from the web system.** It stays one application.
- **A consumer loyalty module in the staff interface.** The instructor says it may come later, and should not be mixed in.
- **More services for their own sake.** The instructor names integration as the challenge, not their number.
- **Firebase push**, unless F27-10 decides otherwise.

## 10. Success criteria

The delivery is complete when the instructor's sixteen acceptance tests pass on
one identified version, on the deployed system, from a clean environment: Android
sale, web parity, desktop read, RFM, migration, incremental, K-means, drift,
recommendations, A/B campaign, exposure and conversion, privacy, revoked session,
disconnection, deployed integration and reproducibility. Section 11 traces each
one, and every other requirement, to its stories.

No conclusion that Rust beats Flask from its language alone. No conclusion that
K-means beats RFM because its groups differ. No concept drift claimed without a
monitored predictive relation. No treated group's sales presented as revenue the
campaign caused.

## 11. Traceability

### Retrospective findings

| ID | Requirement | Stories |
|---|---|---|
| RT-01 | Gateway: published routes, permissions, an authenticated operation from each client on the instance | F20-01, F20-02 |
| RT-02 | Suites repeated on reproducible infrastructure, real defects found, one consolidated result, the two campaign failures | F20-03, F20-04, F20-05, F20-12, F29-07 |
| RT-03 | Parity of RFM, inventory, audit, segments and recommendations; Redis Streams propagation delay | F20-06, F20-07, F20-08, F20-09, F20-10, F20-11, F22-17 |
| RT-04 | Dynamic segmentation against manual recalculation | F21-01, F22-01, F22-02, F22-05, F22-06, F22-07, F22-08, F22-15, F22-17 |
| RT-05 | Statistical validation of K-means | F23-01, F23-02, F23-03, F23-04, F23-05, F23-06 |
| RT-06 | Concept drift told apart from migration and data drift | F23-07, F23-09, F23-10, F23-11 |
| RT-07 | Recommendations bound to inventory, explained, and updated by stock | F21-06, F21-07, F21-10, F24-01, F24-02, F24-04, F24-05, F24-08 |
| RT-08 | Campaign measurement and incremental conversion | F25-01, F25-02, F25-04, F25-08, F25-09, F25-10, F25-11, F25-12, F25-13 |
| RT-09 | Flask and Rust comparison under equal conditions | F15-03 |
| RT-10 | Integration and user experience: full demonstration and tests with real users | F27-13, F28-01, F29-08, F29-09, F29-10, F29-11 |

### The fifteen services

| ID | Requirement | Stories |
|---|---|---|
| SV-01 | auth | F29-11 |
| SV-02 | users | F19-03, F19-04, F19-05, F19-06, F19-07 |
| SV-03 | products | F21-09, F21-10 |
| SV-04 | categories | F29-11 |
| SV-05 | stores | F21-11, F21-12 |
| SV-06 | stock | F21-04, F21-05, F21-06, F21-07, F21-08 |
| SV-07 | customers | F21-13, F21-14, F21-15, F21-19 |
| SV-08 | customer-preferences | F21-15, F21-16, F21-17, F21-18 |
| SV-09 | sales | F21-01, F21-02, F21-03, F25-05 |
| SV-10 | segments | F22-12, F22-14, F22-15, F22-16 |
| SV-11 | recommendations | F24-02, F24-03, F24-04, F24-05, F24-06 |
| SV-12 | engagement | F21-20, F21-21 |
| SV-13 | check-ins | F21-22, F21-23 |
| SV-14 | campaigns | F25-03, F25-04, F25-05 |
| SV-15 | audit-trail | F21-24, F21-25 |

### Capabilities 16 to 25

| ID | Requirement | Stories |
|---|---|---|
| CP-16 | rfm-engine | F19-02, F22-01, F22-03, F22-04, F22-05, F22-09 |
| CP-17 | segment-migration | F19-02, F22-14, F22-16 |
| CP-18 | drift-monitoring | F19-02, F23-07, F23-08, F23-09, F23-11 |
| CP-19 | model-registry | F19-02, F22-02, F22-10, F22-11, F22-12, F22-13 |
| CP-20 | experimentation | F19-02, F25-01, F25-06, F25-07, F25-08, F25-09, F25-13 |
| CP-21 | conversion-attribution | F19-02, F25-02, F25-06, F25-12 |
| CP-22 | analytics-kpis | F19-02, F26-01, F26-02, F26-03, F26-04, F26-05 |
| CP-23 | inventory-personalization | F19-02, F24-02, F24-03, F24-04, F24-07 |
| CP-24 | data-quality | F19-02, F26-06, F26-07, F26-08, F26-09 |
| CP-25 | privacy-consent | F19-02, F21-15, F21-16, F21-17, F21-19, F24-06 |

### The four key capabilities

| ID | Requirement | Stories |
|---|---|---|
| KC-1 | Incremental update | F22-01, F22-05, F22-06, F22-07, F22-08, F22-15, F29-10 |
| KC-2 | Consistent history per model version | F22-02, F22-09, F22-13, F22-14, F22-15, F22-16, F29-10 |
| KC-3 | Personalization bound to inventory | F24-02, F24-04, F24-05, F24-07, F29-10 |
| KC-4 | Experiments with reliable measurement | F25-01, F25-08, F25-10, F25-11, F25-13, F29-10 |

### Android profiles

| ID | Requirement | Stories |
|---|---|---|
| AP-1 | Android: store seller | F19-03, F19-04, F27-01, F27-02, F27-03, F27-04, F27-05, F27-06 |
| AP-2 | Android: store manager | F19-03, F27-01, F27-04, F27-08 |
| AP-3 | Android: loyalty officer | F19-03, F19-04, F27-01, F27-09 |
| AP-4 | Android: commercial supervisor | F19-03, F19-04, F27-01, F27-10, F27-11, F27-12, F27-14 |

### Android technical functions

| ID | Requirement | Stories |
|---|---|---|
| AT-01 | Android: authentication | F27-01 |
| AT-02 | Android: identification | F21-13, F27-02 |
| AT-03 | Android: commercial profile | F27-03 |
| AT-04 | Android: catalog and inventory | F27-04 |
| AT-05 | Android: recommendations | F27-06 |
| AT-06 | Android: sales | F25-05, F27-05 |
| AT-07 | Android: campaigns | F25-03, F27-07 |
| AT-08 | Android: preferences | F27-09 |
| AT-09 | Android: synchronization | F27-13 |
| AT-10 | Android: notifications | F27-10, F27-15 |

### Android rules

| ID | Requirement | Stories |
|---|---|---|
| AR-1 | Android consumes auth, customers, products, stock, sales, recommendations and campaigns | F27-07, F29-10 |
| AR-2 | No sale from a scan; impressions, visits, interactions, carts and transactions are distinct | F21-20, F21-21, F27-05, F27-06, F27-07 |

### Desktop modules

| ID | Requirement | Stories |
|---|---|---|
| DM-01 | Desktop: import and data quality | F26-06, F26-09, F28-02 |
| DM-02 | Desktop: customer exploration | F28-03 |
| DM-03 | Desktop: RFM configuration | F28-04 |
| DM-04 | Desktop: K-means clustering | F28-05 |
| DM-05 | Desktop: migration matrix | F28-06 |
| DM-06 | Desktop: drift analysis | F28-07 |
| DM-07 | Desktop: recommendations | F28-08 |
| DM-08 | Desktop: campaigns | F28-09 |
| DM-09 | Desktop: A/B experiments | F28-10 |
| DM-10 | Desktop: conversion analysis | F28-11 |
| DM-11 | Desktop: inventory planning | F26-05, F28-12 |
| DM-12 | Desktop: model comparison | F28-13 |
| DM-13 | Desktop: executive reports | F28-14 |
| DM-14 | Desktop: audit | F28-15 |

### Desktop rules

| ID | Requirement | Stories |
|---|---|---|
| DX-1 | Desktop: XML only, XSD validation, same JWT and permissions | F22-03, F22-10, F23-08, F24-03, F25-03, F25-06, F26-01, F26-07, F28-01 |
| DX-2 | Desktop: errors, loading, pagination, filters, search, authorized exports, session expiry | F28-01 |
| DX-3 | Desktop: context isolation, restricted IPC, no privileged access from remote content | F28-16 |

### Algorithm validation

| ID | Requirement | Stories |
|---|---|---|
| AV-1 | Validation: RFM | F29-01 |
| AV-2 | Validation: K-means | F23-01, F23-02, F23-03, F23-04, F23-05 |
| AV-3 | Validation: migrations | F29-02 |
| AV-4 | Validation: incremental update | F22-08 |
| AV-5 | Validation: drift detection | F23-10 |
| AV-6 | Validation: recommendations | F24-01, F24-08 |
| AV-7 | Validation: A/B testing | F25-14 |
| AV-8 | Validation: attribution | F25-02, F25-12, F25-14 |
| AV-9 | K-means against RFM on comparable criteria | F23-06 |

### Acceptance tests

| ID | Requirement | Stories |
|---|---|---|
| AC-01 | Android sale | F20-01, F29-03 |
| AC-02 | Web parity | F20-07, F29-03 |
| AC-03 | Desktop read | F20-02, F29-03 |
| AC-04 | RFM | F29-01, F29-04 |
| AC-05 | Migration | F22-15, F29-02, F29-04 |
| AC-06 | Incremental | F22-08, F29-04 |
| AC-07 | K-means | F22-12, F23-04, F29-04 |
| AC-08 | Drift | F23-10, F29-04 |
| AC-09 | Recommendations | F24-01, F24-04, F24-08, F29-05 |
| AC-10 | A/B campaign | F25-08, F25-14, F29-05 |
| AC-11 | Exposure and conversion | F25-10, F25-11, F25-14, F29-05 |
| AC-12 | Privacy | F19-07, F21-14, F21-17, F29-06 |
| AC-13 | Revoked session | F29-06 |
| AC-14 | Disconnection | F27-13, F29-06 |
| AC-15 | Deployed integration | F20-01, F20-02, F29-07 |
| AC-16 | Reproducibility | F20-03, F29-07 |

### The complete process

| ID | Requirement | Stories |
|---|---|---|
| PF-1 | The complete process: purchase, profile, migration, recommendations, measured campaign | F29-10, F29-12 |

## 12. Open questions

Each carries a working assumption or a decision, and the work continues.

| ID | Question | Working assumption |
|---|---|---|
| Q-15 | When is the delivery, and what is the sprint calendar? | Pending. No date is written until the team sets one |
| Q-16 | Which capabilities get a container? | Section 5, until F19-02 is accepted |
| Q-17 | Is the web still frozen, and does it expose APIs? The brief lists "Web and APIs" for it | It stays one application that serves HTML, with no Redis and no service calls, and the APIs are the services'. It changes only where a new fact would otherwise make it wrong: reversals (F21-01) and incremental changes (F22-17) |
| Q-18 | Which are the two campaign failures? | **Decided (2026-10-31).** Nobody on the team can identify the report, so it is not looked for. F20-05 names any campaign failure from a fresh run |
| Q-19 | Can real store staff join the usability tests? | F29-08 states who took part, and declares anyone who only stands in for a role |
| Q-20 | Does a generated dataset satisfy the statistical validation? | Yes, marked synthetic (ADR-0052), with a public retail dataset as an optional second source (F23-01) |
| Q-21 | What are a customer's loyalty "benefits"? | The promotions that apply to them. There is no points model unless the instructor asks for one (F27-09) |
| Q-22 | What are "commercial rules per establishment"? | The maximum discount a store allows, enforced by F25-05 (F21-11) |
