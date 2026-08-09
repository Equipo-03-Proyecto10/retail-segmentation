<!--
Title: Conventional Commits — type(scope): subject (#issue)
Branch: feature/<issue>-kebab-slug, or fix/ chore/ docs/
Target: develop. Never main — main receives merges from develop at a sprint
boundary, tagged.
-->

## What this changes

<!-- One paragraph. What a reviewer needs before reading the diff: what changed
and why now. If the change follows or supersedes a decision, name it —
"D-04", "ADR-001", "R-18" — so the reviewer can check it against the source. -->

Closes #

## How it was verified

<!-- Commands run and what they produced. "Tested locally" is not verification.
If a criterion is executable — a script, a query, a check number — say which. -->

---

## Definition of Done

The list is §6 of `docs/scrum/00-scrum-framework-charter.md`, verbatim. **`n/a`
is a valid answer and is better than a tick you cannot defend** — write it next
to the item with the reason. A story failing any applicable item returns to the
backlog and does not count toward velocity.

- [ ] 1. Every acceptance criterion is verified by a team member other than the author.
- [ ] 2. Code merged into `develop` through a pull request with at least one approval. Feature branch deleted.
- [ ] 3. Any database schema change ships as an Alembic migration. `alembic upgrade head` succeeds against an empty database.
- [ ] 4. A story that changes the database schema ships an updated `infra/sql/schema/verify_m1_schema.sql` that passes.
- [ ] 5. The feature runs from a clean clone with no manual steps beyond `docker compose up`, migration, and seed.
- [ ] 6. Every state-changing operation writes an audit log record identifying the actor, the action, the target entity, and the timestamp.
- [ ] 7. Unit tests exist for business logic. The `pytest` suite passes.
- [ ] 8. Errors and significant events are logged through `structlog` with a request or correlation identifier.
- [ ] 9. No secrets in source. New configuration variables are documented in `.env.example`.
- [ ] 10. User-facing screens verified at 375 px and 1440 px viewport widths.
- [ ] 11. The issue is closed with attached evidence: a screenshot, test output, or short screen recording.

## Hard rules a reviewer has to check actively

These fail quietly. Ticking them without looking is how they get through.

- [ ] **Segment assignments are never updated in place.** No mutable `customer.segment_id`. A run closes the previous assignment with `valid_to` and inserts a new row; the `EXCLUDE USING gist` constraint stays intact.
- [ ] **Migration compares `segment_label.code`, never `segment_id`.** Segments are run-scoped, so a `segment_id` comparison reports every customer as migrated on every run — and errors on nothing.
- [ ] **One writer per table.** Checked against `docs/adr/0002-data-ownership-map.md` before writing to any store from a new component.
- [ ] **No raw DDL outside a migration**, and no table edited by hand.
- [ ] **`services/_shared/` is the only home for cross-service code.** Not copied into a service.
- [ ] **No datasets committed.** `data/` and `*.csv` are gitignored for a reason.
- [ ] **English only.** Code, comments, commit messages, branch names, documentation.

## Evidence

<!-- DoD item 11. Screenshot, test output, or recording. Paste it here so the
issue can be closed against something rather than against a claim. -->

---

<!-- Working agreements: reviewed within 24 hours on weekdays; a PR open longer
than 48 hours is raised at the Weekly Sync. A PR over 400 changed lines should
probably have been split — if this one is, say why it could not be. -->
