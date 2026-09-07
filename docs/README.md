# Documentation

| Document | Purpose |
|---|---|
| [Scope](scope.md) | What the delivery includes, the constraints, the phases, the deliverables |
| [Process](process.md) | How the team works: Scrum planning, XP engineering practices, Definition of Done |
| [Backlog](backlog.md) | Ordered work, grouped by scope phase |
| [Roadmap](roadmap.md) | What is deferred to later deliveries, and how it re-enters |
| [Infrastructure](infra.md) | Provisioned GCP resources, firewall policy, and SSH access |
| [PostgreSQL access](../deploy/postgresql/README.md) | Loopback and SSH access, HBA rejection checks, and application-role verification |
| [Logging](logging.md) | The application event log: what each event records, at which level, and what never reaches it |
| [Requirements](requirements.md) | Functional and non-functional requirements, the permission matrix, and traceability to the demonstration |
| [User stories](user-stories.md) | What each role needs from the application, with acceptance criteria |
| [Business rules](business-rules.md) | The invariants, and where each one is actually enforced |
| [Data model](data-model.md) | The PostgreSQL model: conceptual, 4NF normalization with its justification, ER diagram, data dictionary |
| [Datastore designs](datastores/) | MongoDB and Redis designs for later deliveries. Nothing in there is implemented |
| [Design system](design-system/) | MOSAIQ's tokens, components, and the two reference sheets to build screens from |
| [Decisions](adr/) | Architecture Decision Records |
| [Evidence](evidence/) | Observed results per story: database access, integrity, screenshots, negative tests, review passes, deployment |
| [Issue history](issue-history.md) | What the deleted `#1`–`#40` references in older commits pointed at |

Documents that do not exist yet are produced by the story that needs them:

| Deliverable | Produced by |
|---|---|
| Final verification of the published delivery | F6-05 (#81) — [partly recorded](evidence/f6-05-final-verification.md); the published `docs/` copy is a stale snapshot |

Everything is written in English — code, comments, commits, issues,
documentation. Spoken meetings are in Spanish.
