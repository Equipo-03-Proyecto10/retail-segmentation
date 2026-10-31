# Documentation

| Document | Purpose |
|---|---|
| [Scope](scope.md) | What the first delivery includes, the constraints, the phases, the deliverables |
| [Scope — Delivery 2, analytics](scope-delivery-2-analytics.md) | The web system's analytics phase (phases 7–12) |
| [Scope — Delivery 2, distributed](scope-delivery-2-distributed.md) | Microservices, Android and Electron clients, Redis, MongoDB and JWT (phases 13–18) |
| [Scope — Delivery 3](scope-delivery-3.md) | The commercial intelligence platform: the instructor's retrospective checked against the repository, and phases 19–29 |
| [Analytics permission map](analytics-permission-map.md) | Which permission each analytics surface requires |
| [Process](process.md) | How the team works: Scrum planning, XP engineering practices, Definition of Done |
| [Backlog](backlog.md) | Ordered work, grouped by scope phase |
| [Roadmap](roadmap.md) | What the second delivery requires, where each part stands, and how each kind of work enters |
| [Infrastructure](infra.md) | Provisioned GCP resources, firewall policy, and SSH access |
| [PostgreSQL access](../deploy/postgresql/README.md) | Loopback and SSH access, HBA rejection checks, and application-role verification |
| [Logging](logging.md) | The application event log: what each event records, at which level, and what never reaches it |
| [Requirements](requirements.md) | Functional and non-functional requirements, the permission matrix, and traceability to the demonstration |
| [User stories](user-stories.md) | What each role needs from the application, with acceptance criteria |
| [Business rules](business-rules.md) | The invariants, and where each one is actually enforced |
| [Architecture](architecture/) | Application, deployment and infrastructure views of the web system and the fifteen services |
| [System and demonstration guide](demo-system-guide.md) | End-to-end request journey, current capabilities, main process, architecture decisions, limitations, and rehearsal order. The deliverable of F6-08 (#110) |
| [Data model](data-model.md) | The PostgreSQL model: conceptual, 4NF normalization with its justification, ER diagram, data dictionary |
| [Datastore designs](datastores/) | MongoDB and Redis. Governed by ADR-0037 and ADR-0038, and rewritten as specifications by F13-05 and F13-06 |
| [Wireframes](wireframes/) | Low-fidelity structure for every screen — what is on each page and how a role reaches it, decided before the visual design |
| [Design system](design-system/) | MOSAIQ's tokens, components, and the two reference sheets to build screens from |
| [Services](services/) | One sheet per microservice, all with the same structure, and the register of accepted exceptions |
| [Decisions](adr/) | Architecture Decision Records |
| [Evidence](evidence/) | Observed results per story: database access, integrity, screenshots, negative tests, review passes, deployment |
| [Issue history](issue-history.md) | What the deleted `#1`–`#40` references in older commits pointed at |

Documents that do not exist yet are produced by the story that needs them:

| Deliverable | Produced by |
|---|---|
| Final verification of the published delivery | F6-05 (#81) — [recorded](evidence/f6-05-final-verification.md); AC 3 needs a signed-in session |

Everything is written in English — code, comments, commits, issues,
documentation. Spoken meetings are in Spanish.

Run `python docs/check.py` to verify that business-rule ids are unique and that
current table-count statements agree with `sql/01_schema.sql`.
