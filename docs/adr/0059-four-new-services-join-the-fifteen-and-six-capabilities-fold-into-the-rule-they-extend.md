# ADR-0059 — Four new services join the fifteen, and six capabilities fold into the rule they extend

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #752
**Supersedes:** in part, [ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md) (the registry's fifteen), [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md) (the 3,000 MB gate and what to drop when memory runs short) and [ADR-0043](0043-at-least-ten-microservices-each-implementing-one-business-rule.md) (the plan of fifteen, the cut order and the next free port)
**Superseded by:** —

---

## Context

The instructor's brief for the third delivery widens the fifteen services and
recommends ten more capabilities, numbered 16 to 25 on ports 5016 to 5025. It says
the ports are proposals, that not all of them need a container, that some can be
integrated in segments, sales, recommendations or campaigns to avoid artificial
fragmentation, and that the main challenge is not more services.

Three things decide where each one goes:

1. **One rule per service still binds.** ADR-0043 defines a rule as one invariant
   on data a service writes, or one read rule. It also says services are never
   merged to save memory, because a merge mixes rules. So a capability may fold
   into a service only when it is the same rule applied to more facts.
2. **The code to port is Python.** RFM and K-means live only in the web, and
   segments is a read-only Rust service. ADR-0045 lets a service port a rule pinned
   by golden fixtures. K-means is reproducible through Python's `random.Random`, so
   a Rust port would first have to reproduce that generator exactly.
3. **Memory is nearly spent.** The `mem_limit`s sum to 2,832 MB against the 3,000 MB
   gate, which leaves room for one Flask service at 160 MB.

Two parts are judgement calls. F18-01 measured the Flask services at about 83 MiB,
but idle, so the numbers below are limits and not a measured need; F19-08 measures
under load before any new service deploys. And folding privacy-consent is the
weakest case here, as the table below says.

## Decision

The plan moves from fifteen services to nineteen. Four capabilities become new
containers, registered as `wave: third` on the instructor's proposed ports, and six
fold into an existing rule.

| Service | Port | Language | Role and schemas | One rule | Top-level resources | `mem_limit` |
|---|---|---|---|---|---|---|
| rfm-engine | 5016 | Flask | `svc_rfm`, `rfm`, `api_rfm` | A customer's R, F and M are a function of their accepted, unreversed purchases under one rule-set version, change only for the customers an event touched, and equal a full recalculation of that version at the same instant (RN-65) | `rfm-profiles`, `rfm-rule-sets`, `rfm-recalculations` | 160 MB |
| model-registry | 5019 | Flask | `svc_models`, `models`, `api_models` | A model version is immutable once published, carries its dataset, parameters, metrics and reference distribution, changes state only by an explicit publication or rollback, and every drift alert is judged against that reference (RN-66) | `model-versions`, `model-trainings`, `drift-reports`, `drift-alerts` | 160 MB |
| experimentation | 5020 | Flask | `svc_experiments`, `experiments`, `api_experiments` | An experiment's frame and arms are fixed before measurement, assignment, exposure and conversion are separate append-only facts, a sale is attributed at most once, and an effect is reported only against a control, with its uncertainty (RN-23, RN-24, RN-25, RN-27, RN-42, RN-48, RN-49, RN-67) | `experiments`, `experiment-assignments`, `experiment-results`, `sale-attributions` | 160 MB |
| analytics-kpis | 5022 | Rust | `svc_kpis`, `kpis`, `api_kpis` | A KPI is reported only under its published definition, window and data origin, from `api_*` objects, and only to the roles and stores allowed to read it (RN-68) | `kpi-reports` | 64 MB |

RN-65 to RN-68 are written by each service's contract story. analytics-kpis is Rust
because it has no Python rule to port, and it reads like segments and audit-trail.

| Capability | Folds into | How the registry's rule changes | Why it is still one rule |
|---|---|---|---|
| 17 segment-migration | segments | Adds that a migration compares one customer's labels within one model version, and that a comparison across versions is marked methodological (RN-39, RN-59, RN-60) | A read rule over the assignment history segments already owns |
| 18 drift-monitoring | model-registry | Already in its rule above | A version carries its reference distribution, so drift is a judgement of that version |
| 21 conversion-attribution | experimentation | Already in its rule above | Attribution and conversion link a sale to the same assignments, and one sale counted once is the same measured fact |
| 23 inventory-personalization | recommendations | RN-40 is restated by F24-02 | Availability is RN-40's own eligibility test, not a second rule |
| 24 data-quality | sales | Adds that a file's rows pass the same rule as a recorded sale (RN-37) | An import is a batch of sales, each accepted or refused by the same rule |
| 25 privacy-consent | customer-preferences and customers | customer-preferences adds that consent and restrictions change only as recorded facts; customers adds that it anonymizes and never deletes | **The weakest fold.** Consent is append-only and preference sets are replaced whole, so these are arguably two invariants. If a reviewer rejects it, consent becomes a Rust service on 5025, at 48 MB, under this same budget |

**Memory.** The four add 544 MB, so the `mem_limit`s sum to 3,376 MB and the gate
moves to 3,500 MB. With the host's 2,690 MB the VM commits about 6.1 GB of 8 GB, and
the 2 GB swapfile stays a cushion. The gate moves in F19-08, which also measures
under load, and no new service deploys before it.

**If memory runs short,** lower the Flask limits to 128 MB after measuring, which
saves up to 288 MB across nine services. Then consider rfm-engine in Rust, for 112 MB
more. ADR-0043's cut order no longer holds: campaigns and audit-trail cannot be
dropped, since the instructor's catalog lists both and Android must consume
campaigns. No service is removed or merged to save memory.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| All ten as containers, on the proposed ports | Ten Flask services add 1,600 MB, for 4,432 MB in containers and 7.1 GB committed of 8 GB, with no margin. Migration, attribution and inventory filtering would own no data of their own, which is the fragmentation the instructor warns about |
| No new container: fold everything into the four services the instructor names | segments would become a stream worker, a trainer and a drift job in Rust, and would need a port of `random.Random` to keep K-means pinned. campaigns would mix its lifecycle with the experiment rules ADR-0043 would keep apart. KPIs that read many boundaries belong to none of the four |
| drift-monitoring as its own container on 5018 | A defensible read rule, but 160 MB for a scheduled job whose reference distribution is part of a model version. It is the cheapest fold to reverse later |
| experimentation folded into campaigns, as the instructor's catalog suggests | It saves 160 MB, but a campaign's lifecycle and the append-only facts of an experiment are different invariants, and ADR-0043 forbids a merge that mixes them |
| rfm-engine in Rust from the start | It saves 112 MB. It is held back as the second memory cut, because the rule to port is Python and golden parity is simpler to reach in the same language |

## Consequences

**What this makes easy.** Each capability has one home. Phases 22 to 26 can write
their contracts, and the catalog can be traced to a service line by line.

**What this makes hard.** Four more services need the stand-up work of F22-04: a
contract, a schema section, a compose project, an NGINX include, an ACL line and a
deployment. The instance's margin falls by about 500 MB of committed memory. segments
and sales grow, and their rule sentences have to stay honest one-sentence rules.

**What must now be true elsewhere.**
- Each service's contract story adds its `contracts/registry.yaml` entry, its RN id
  and its service sheet.
- `AGENTS.md`, `docs/scope-delivery-3.md` §5 and every "fifteen" in the current
  documents are updated as the services arrive, and F29-11 checks them.
- ADR-0043's sixteenth-service checklist still applies to each new service, except
  that the port is the one above and not the next free one.
- `web/` stays free of every new service (ADR-0045).

## Compliance

`contracts/check.sh` proves each new resource has one owner once its entry merges.
`.github/scripts/memory_gate.py` enforces the new sum after F19-08. `docs/scope-delivery-3.md`
§5 must state the same placement. When this record is accepted, its status changes
in the same pull request, and only the status lines of ADR-0034, ADR-0040 and
ADR-0043 are edited.
