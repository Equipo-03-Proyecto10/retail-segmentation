# MongoDB collection design

**Status:** Stub — awaiting S0-04b
**Owner:** Estefanía
**Story:** S0-04b · **Issue:** #22 · **Due:** Wed 2026-08-12
**Milestone 1 deliverable:** 8

---

> **This is a stub.** It carries the structure, the constraints already decided
> elsewhere, and the acceptance criteria, so the author starts from a shape
> rather than a blank file. S0-04b is an owned, estimated story and the design
> content is written there, not here.
>
> It exists because `docs/data/postgresql-model.md` §5.4 and
> `docs/scrum/02-release-plan-and-sprint-calendar.md` §4 both reference this
> path as if the file were present, and because nothing downstream should stall
> waiting for it to be created.

## Context

*To be written in S0-04b.*

## Constraints already fixed elsewhere

These are not open questions. They are decisions taken in S0-04a and recorded in
`postgresql-model.md`; the design in this document has to be consistent with
them.

| Constraint | Source |
|---|---|
| MongoDB holds per-row ingestion rejections, model run telemetry, recommendation events and inventory history — data whose shape genuinely varies per source | §5.4 |
| PostgreSQL keeps the counts that must join: `rows_read`, `rows_accepted`, `rows_rejected`, `rows_duplicate` on `ingestion_run` | §5.4 |
| The crossing point is `ingestion_run.telemetry_ref`, `varchar(80)`, holding a MongoDB `ObjectId` as a plain string | §5.4, physical model |
| **No referential integrity is claimed across stores.** Nothing may be designed here that assumes a foreign key into PostgreSQL | §5.4 |
| `recommendation_event` belongs in MongoDB and not in PostgreSQL, on volume and shape grounds | §5.4, and the store-placement table |
| One writer per collection. The writing component is assigned in ADR-002, not here | `AGENTS.md`, `../adr/0002-data-ownership-map.md` |

## Collections

The four collections S0-04b names. Each needs a stated purpose, a document
shape, and a justification for why it is not a relational table — the third is
the part the course actually grades, and "it is faster" is not a justification.

### `ingestion_run`

*Purpose, document shape and non-relational justification to be written in S0-04b.*

Note the name collision with the PostgreSQL `ingestion_run` table. They are not
the same record: the relational row owns the auditable status and counts, this
document owns the variable-shape detail. State the relationship explicitly so a
reader does not assume one is a copy of the other.

### `ingestion_error`

*To be written in S0-04b.*

The per-row rejection detail behind `rows_rejected`. This is what the rejection
report in the Sprint 1 upload path reads.

### `model_run_telemetry`

*To be written in S0-04b.*

Per-run diagnostics for `segmentation_model_run`. Note that the reproducibility
set — `algorithm_version`, `library_version`, `random_seed`, `scaler_state`,
`feature_set_version` — is already relational and must not be duplicated here.

### `recommendation_event`

*To be written in S0-04b.*

M2 scope, designed now so the boundary is settled before the microservices are
built.

## Indexes

*To be written in S0-04b.*

## Retention

*To be written in S0-04b.* A-08 in `../scrum/assumption-register.md` records the
working assumption that retention is indefinite for M1 with a documented policy;
if this design departs from that, update the register rather than contradicting
it silently.

## Acceptance criteria

Carried from S0-04b in `../scrum/03-sprint-00-backlog.md`. The document is not
complete until both hold.

- [ ] Given the MongoDB design, when reviewed, then **every collection has a stated purpose, a document shape, and a justification for why it is not a relational table**
- [ ] Given the design, when compared against `postgresql-model.md` §5.4, then no collection claims referential integrity into PostgreSQL

## References

- `../scrum/03-sprint-00-backlog.md` — S0-04b
- `postgresql-model.md` §5.4 — the store boundary and the crossing point
- `../adr/0002-data-ownership-map.md` — assigns the writing component per collection
- `redis-design.md` — the other half of S0-04b
