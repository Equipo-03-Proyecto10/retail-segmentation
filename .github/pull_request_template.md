<!--
Title: Conventional Commits — type(scope): subject (#issue)
Branch: feature/<issue>-kebab-slug, or fix/ chore/ docs/
Target: develop.
-->

## What this changes

<!-- One paragraph: what changed and why now. If it follows or supersedes a
decision, name it so the reviewer can check it against the source. -->

Closes #

## How it was verified

<!-- Commands run and what they produced. "Tested locally" is not verification. -->

---

## Definition of Done

`docs/process.md` §6. **`n/a` with a reason is better than a tick you cannot
defend.**

- [ ] 1. Every acceptance criterion verified by someone other than the author.
- [ ] 2. Merged into `develop` via reviewed pull request; feature branch deleted.
- [ ] 3. Schema changes are in `sql/01_schema.sql`, and the three scripts run clean in order from an empty database.
- [ ] 4. `02_seed_30_per_table.sql` still loads, at least 30 rows per table.
- [ ] 5. Runs from a clean clone by following the README, no undocumented steps.
- [ ] 6. Tests exist for business logic and `pytest` passes.
- [ ] 7. `black --check .` and `ruff check .` pass.
- [ ] 8. Input validated and sanitized; every SQL statement parameterized.
- [ ] 9. No secrets in source; new configuration in `.env.example`.
- [ ] 10. Screens verified at 375 px and 1440 px.
- [ ] 11. Issue closed with evidence attached.

## Rules a reviewer has to check actively

These fail quietly. Ticking them without looking is how they get through.

- [ ] **No SQL built by string interpolation.** Parameters everywhere, including in scripts.
- [ ] **The single-administrator rule still holds in both halves** — refused by the application *and* by the partial unique index.
- [ ] **No second deployable unit, no external API, no JSON or XML between internal components.**
- [ ] **No DDL outside `sql/01_schema.sql`**, and no table altered by hand on the server.
- [ ] **No datasets, uploads or secrets committed.**
- [ ] **English only** — code, comments, commits, branch names, documentation.

## Evidence

<!-- Item 11. Screenshot, test output, or recording — so the issue closes
against something rather than against a claim. -->

---

<!-- Reviewed within one working day. A PR over 400 changed lines should have
been split; if this one is, say why it could not be. -->
