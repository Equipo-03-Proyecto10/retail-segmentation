# Issue history — the retired scope

The 30 issues from the four-component scope were deleted when the project became
a monolith. GitHub never reuses issue numbers, so `#1`–`#40` can never exist
again and the references to them in commit messages before `c4a2b63` do not
resolve.

This table is what those references pointed at. Pull request numbers were not
deleted: `#11`, `#13`, `#15`, `#34`–`#39`, `#41` and `#44` still resolve, with
their diffs and descriptions intact.

| Was | Subject | Superseded by |
|---|---|---|
| #1 | S0-04: frozen PostgreSQL model, DDL and verification script | Retired. Design in history at `c4a2b63`, `docs/data/` |
| #2 | S0-04: create the Assumption Register | `docs/scope.md` §8 open questions |
| #3 | S0-04: product attribute JSONB schema | Retired |
| #4 | S0-04: ADR index and template | `docs/adr/README.md`, `docs/adr/template.md` |
| #5 | S0-04: ADR stubs for S0-05, S0-06, S0-07, ADR-004 | Retired — all four ADRs described components now out of scope |
| #6 | Correct 03-sprint-00-backlog.md against the data model | Retired with the document |
| #7 | Correct 01-product-backlog-epics.md against the data model | Retired with the document |
| #8 | Correct 04-risk-register.md against the data model | Retired with the document |
| #9 | Correct 02-release-plan-and-sprint-calendar.md | Retired with the document |
| #10 | Correct 00-scrum-framework-charter.md | `docs/process.md` |
| #12 | AGENTS.md states the partial unique index as a hard rule | `AGENTS.md`, now about the single administrator |
| #14 | S0-04a: first substantive Alembic revision | Retired. Alembic replaced by `sql/` scripts (ADR-0001) |
| #16 | S0-01: issue templates for user story and defect | `.github/ISSUE_TEMPLATE/` |
| #17 | S0-10: pull request template with the DoD checklist | `.github/pull_request_template.md` |
| #19 | S0-01: repository, organization and delivery board | F0-01 (#45) |
| #20 | S0-02: local container stack | Retired. No containers — gunicorn under systemd on the instance |
| #21 | S0-03: Flask application skeleton | F3-01 (#61) |
| #22 | S0-04b: MongoDB and Redis design | Retired. One database engine only |
| #23 | S0-05: ADR-001 authentication and token lifecycle | F3-03 (#63), session-based rather than JWT |
| #24 | S0-06: ADR-002 data ownership map | Retired. One writer, one application |
| #25 | S0-07: ADR-003 API standards and content negotiation | Retired. External APIs and JSON/XML exchange prohibited |
| #26 | S0-08: seed dataset with realistic transaction history | F2-06 (#59) |
| #27 | S0-09: baseline architecture diagrams | F2-03 (#56) |
| #28 | S0-10: engineering standards and Definition of Done | `docs/process.md` §6 |
| #29 | S0-11: requirements and analysis skeleton | F2-01 (#54) |
| #30 | Add Raquel and Estefanía to the GitHub organization | Done |
| #31 | Planning documents contradict on commitment and owners | Retired with the documents |
| #32 | verify_m1_schema.sql is not run by CI | `.github/workflows/ci.yml`, now checking the `sql/` scripts |
| #33 | Charter Appendix C references a template that does not exist | Retired with the charter |
| #40 | Deterministic cluster-to-label mapping rule | Deferred. `docs/roadmap.md` carries the rule that matters |

The scope change itself is recorded in
[ADR-0001](adr/0001-flask-monolith-on-a-single-vm.md).
