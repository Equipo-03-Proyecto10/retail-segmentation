# Contributing

## Setup

```bash
cp .env.example .env
make up && make migrate && make seed
```

Using an AI coding agent: `touch ~/.claude/rs-local.md`

## Branch flow

`main` (protected, tagged releases) ← `develop` (integration) ← `feature/<issue>-slug`

```bash
git checkout develop && git pull
git checkout -b feature/42-add-refresh-token-rotation
```

## Pull requests

- One approval minimum. Reviewed within 24 hours on weekdays.
- Over 400 changed lines means it should have been split.
- Fill the Definition of Done checklist in the description.
- Reference the issue: `feat(auth): add refresh token rotation (#42)`

## Rules that will get a PR rejected

- Schema change without an Alembic migration
- `UPDATE` on `customer_segment_assignment` instead of close-and-insert
- State-changing operation with no audit log record
- Secrets or dataset files committed
- Documentation deliverable missing for a story that requires one

## Escalation

Blocked more than 4 hours: post in the team channel. Do not wait for the
Monday sync.
