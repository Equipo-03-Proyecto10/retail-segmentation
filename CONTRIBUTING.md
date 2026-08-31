# Contributing

## Setup

```bash
cp .env.example .env
python -m venv .venv && source .venv/bin/activate
pip install -r web/requirements-dev.txt
```

Using an AI coding agent: `touch ~/.claude/rs-local.md`

## Branch flow

`main` (protected, tagged) ← `develop` (integration, reviewed) ← `feature/<issue>-slug`

```bash
git checkout develop && git pull
git checkout -b feature/42-add-password-reset
```

Integrate into `develop` at least once per working day. A branch that has not
merged in three days is raised at the Weekly Sync.

## Taking work

Work is pulled by the person who will do it. To take an issue:

1. On the Project board, choose an unassigned issue in the current sprint whose
   status is Ready and which has no Blocked indicator.
2. Open Relationships and confirm that every `Blocked by` issue is closed.
3. Assign yourself and move the issue to In Progress before creating a branch.
4. Create the branch from the current `develop` using the issue number.

Self-assignment does not change the issue's priority or sprint. Do not exceed
the two-story work-in-progress limit. Authentication, the 4NF model, and
deployment require pair programming, so both participants are assigned.

If you stop or hand work over, leave a comment with the current state and any
blocker. Reassign it only after the next person agrees; otherwise unassign
yourself and return it to Ready.

## Pull requests

- One approval minimum, reviewed within one working day.
- Over 400 changed lines means it should have been split.
- Fill the Definition of Done checklist in the description.
- Reference the issue: `feat(auth): add password reset (#42)`

## What will get a PR rejected

- Schema change that is not in `sql/01_schema.sql`, or that breaks a clean run
  of the three scripts in order against an empty database
- A new table without its 30 seed rows
- SQL built by string interpolation instead of parameters
- A second administrator made possible, in the application or in the schema
- Secrets, datasets or uploaded files committed
- Anything written in Spanish
- A documentation deliverable missing for a story that requires one

## Pair programming

Required for authentication, the 4NF model, and the deployment. Optional
elsewhere. These are the three places where a solo mistake stays invisible until
it is expensive.

## Blocked

Blocked for more than half a working day: post in the team channel. Do not wait
for the Weekly Sync.
