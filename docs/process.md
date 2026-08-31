# Process — Scrum and XP

How Team 03 works. Scrum provides the planning frame — roles, backlog, sprints,
review, retrospective. Extreme Programming provides the engineering practices
that decide whether what gets planned actually ships.

The two are used for what each is good at. Scrum says nothing about how code is
written; XP says very little about how work is prioritized. Taking ceremonies
from one and practices from the other is the point, not a compromise.

Dates and sprint lengths are not fixed yet. Cadence is expressed relative to the
sprint boundary until the team sets a calendar.

---

## 1. Roles

| Role | Person | Responsibility |
|---|---|---|
| Product Owner | Dr. Raúl Morales Saucedo | Owns the requirements and accepts the delivery |
| Proxy Product Owner | Raquel | Day-to-day backlog owner. Writes and prioritizes stories, accepts them against acceptance criteria, resolves scope questions the PO has not answered |
| Scrum Master | Marcelo | Facilitates ceremonies, maintains the board, removes blockers, enforces the Definition of Done |
| Development Team | Raquel, Estefanía, Max, Marcelo | Estimates, commits, builds, tests, demonstrates |

The Product Owner is the course professor and is not available for planning or
same-day scope answers. XP's on-site customer is therefore not available either,
and the Proxy PO exists to fill that gap. Open questions are recorded in
[`scope.md`](scope.md) §8 with a working assumption, and work continues.

## 2. Artifacts

| Artifact | What it is |
|---|---|
| [Scope](scope.md) | The boundary of the delivery. Changing it is a decision, and decisions go in `adr/` |
| [Backlog](backlog.md) | Ordered work, grouped by scope phase |
| Sprint backlog | The subset committed at Planning, tracked on the GitHub Projects board |
| [ADRs](adr/) | Decisions other work inherits |
| [Roadmap](roadmap.md) | What comes after this delivery |

## 3. Ceremonies

| Ceremony | When | Output |
|---|---|---|
| Sprint Planning | First day of the sprint | Sprint Goal, committed backlog, task assignment |
| Weekly Sync | Once per week | Blockers raised, board reconciled |
| Async check-in | Every working day, written | Yesterday / today / blockers, in the team channel |
| Backlog Refinement | Mid-sprint | Next sprint's candidates brought to Definition of Ready |
| Sprint Review | Last day of the sprint | Working software demonstrated, stories accepted or rejected |
| Retrospective | Immediately after Review | Between 1 and 3 improvement actions, each with an owner |

There is no synchronous daily standup. Four people working in staggered blocks
cannot reliably hold one, and a documented daily that does not happen is worse
than an honest weekly. The daily coordination is written and asynchronous.

## 4. XP practices adopted

| Practice | How it applies here |
|---|---|
| **Pair programming** | Required for authentication, the 4NF model, and the deployment. Not required for routine CRUD. These are the three places where a solo mistake is expensive and invisible until late |
| **Test-first** | Business logic gets its test before its implementation. Templates and static assets do not |
| **Continuous integration** | Every push runs the pipeline. Everyone integrates into `develop` at least once per working day. A branch that has not merged in three days is raised at the Weekly Sync |
| **Small releases** | Deploy to the instance as soon as there is something to deploy, then keep deploying. A first deployment attempted near the deadline is the most common way this kind of project fails |
| **Simple design** | Build what the current story needs. The roadmap exists so that deferred work does not get built speculatively |
| **Refactoring** | Continuous, on code covered by tests. Not scheduled as separate stories |
| **Collective ownership** | Anyone may change any file. The reviewer, not the original author, is the gate |
| **Coding standards** | PEP 8, enforced by `black` and `ruff` in CI. Not a matter of taste |
| **Sustainable pace** | Weekend work is recovery, never planned capacity. Two consecutive recovery weekends means the next commitment is reduced |
| **Spikes** | An unknown large enough to block estimation becomes a timeboxed spike whose output is knowledge, not shippable code |

## 5. Definition of Ready

A story may not enter a sprint until:

1. Written as `As a <role>, I want <capability>, so that <benefit>`.
2. Acceptance criteria in Given / When / Then form, each independently testable.
3. Dependencies identified, and any blocker already done or scheduled earlier.
4. Data model impact known: which tables are read and which are written.
5. Estimated by the team.
6. Owner assigned.
7. Fits in one sprint. A story too large to fit is split before it is committed.

## 6. Definition of Done

A story is Done only when all of the following hold. A story failing any
applicable item returns to the backlog and does not count toward velocity.
`n/a` with a reason is a valid answer.

1. Every acceptance criterion is verified by a team member other than the author.
2. Merged into `develop` through a pull request with at least one approval, and the feature branch deleted.
3. Schema changes are reflected in `sql/01_schema.sql`, and the three scripts run clean in order against an empty database.
4. `02_seed_30_per_table.sql` still loads, with at least 30 rows per table.
5. The feature runs from a clean clone by following the README, with no undocumented manual steps.
6. Tests exist for business logic and `pytest` passes.
7. `black --check .` and `ruff check .` pass.
8. Input is validated and sanitized; every SQL statement is parameterized.
9. No secrets in source. New configuration is documented in `.env.example`.
10. User-facing screens verified at 375 px and 1440 px.
11. The issue is closed with evidence attached: a screenshot, test output, or a short recording.

### Sprint level

1. Sprint Goal met, or the gap recorded in the Review notes.
2. `develop` merged to `main` and tagged.
3. Documentation deliverables assigned to the sprint are committed.
4. Retrospective actions recorded with owners.
5. The deployed instance reflects the sprint's work.

## 7. Estimation

Story points on a modified Fibonacci scale (1, 2, 3, 5, 8, 13) set by the whole
team in Planning Poker. Points measure complexity, effort and uncertainty
together — never hours. A 13 is too large and is split before it is committed.

The team has no velocity history. The first sprint is planned conservatively and
the commitment is calibrated from measured velocity afterwards.

## 8. Working agreements

- **Work in progress.** Maximum 2 stories in progress per person. Finish before starting.
- **Pull requests.** Reviewed within one working day. A PR over 400 changed lines should have been split.
- **Branching.** `main` is protected. `develop` is the integration branch and also requires review. Feature branches are `feature/<issue>-kebab-slug`, also `fix/`, `chore/`, `docs/`.
- **Commits.** Conventional Commits: `type(scope): subject (#issue)`.
- **Broken `develop`.** Anyone may declare it broken. Repairing it takes priority over all new work, for everyone.
- **Blocked work.** Blocked for more than half a working day means post in the channel. Do not sit on it until the Weekly Sync.
- **Estimation honesty.** Nobody estimates alone, and nobody revises another person's estimate downward without discussion.
- **Documentation is scope.** A story whose code works but whose documentation deliverable is missing is not Done.

## 9. Tooling

| Purpose | Tool |
|---|---|
| Source control | GitHub, organization-owned monorepo |
| Board | GitHub Projects — Backlog, Ready, In Progress, In Review, Done |
| Issues | GitHub Issues, labelled `type:*`, `area:*`, `priority:*` |
| CI | GitHub Actions |
| Documentation | Markdown in `docs/`, versioned in Git |
| Decisions | ADRs in `docs/adr/` |
| Communication | Team channel |

The board is graded evidence. Two rules protect it: an issue moves to In
Progress only when someone is actually working on it, and nothing is closed
retroactively in bulk. Visible spillover is stronger evidence of a working
process than a suspiciously clean burndown.
