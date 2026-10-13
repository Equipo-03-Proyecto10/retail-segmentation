# ADR-0043 — The distributed stage has at least ten microservices, each implementing one business rule

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md), in part. Only its count of six to ten services
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
reinstated the distributed architecture and sized it at "six to ten services
in individual containers". The earlier drafts of ADR-0034 to ADR-0042 built on
that ceiling. They proposed eight boundaries that grouped related rules and
reused the web's modules.

The instructor has since set four binding requirements for the services, and
the team confirmed them:

1. **Total independence.** Each microservice is decoupled from the others. It
   can grow, and be built, versioned, deployed, scaled and rolled back, on its
   own.
2. **Never mix endpoints.** No service exposes, duplicates, proxies or nests
   another service's resources.
3. **One business rule per microservice,** never two.
4. **At least ten microservices,** with no upper cap. Every one must be a real
   rule, not filler written to reach the number.

The fourth requirement removes ADR-0016's upper bound and turns ten into a
floor. The third makes the count follow the domain: a domain with fifteen
separable rules gets fifteen services. None of the four says how to count a
rule, and "one business rule" is not defined anywhere in the course material.
`docs/business-rules.md` gives the team a vocabulary: every enforced
invariant has an RN id. Some candidate boundaries, however, enforce no write
invariant at all. segments and audit-trail only read and report, under a rule
about what they may say.

The genuine uncertainty is whether the instructor counts such a read-only
boundary as implementing a rule. That question is open as **Q-13** and must
be answered in writing. This record is built to survive either answer.

## Decision

The distributed stage has **at least ten microservices and no upper cap**.
Each implements exactly one business rule and owns its resources
exclusively. The team plans **fifteen**: thirteen core services and two in a
second wave (campaigns and audit-trail), as registered in
[ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md).
A **business rule** here is either one invariant that the service enforces on
data it writes, or one read rule that governs what the service may report and
to whom. Either kind is stated in one sentence and backed by RN ids in
`docs/business-rules.md`. A rule this stage introduces gets its RN id there
no later than the service's registry line. Several RN ids may back one rule
when they are facets of the same invariant: users' single administrator,
unique email and deactivation are all about which accounts may exist. Two
facts that change independently are two rules, and so two services. That is
why products and categories, and customers and customer-preferences, are
separate. Read-only boundaries count, pending Q-13. If memory runs short,
the second wave is dropped first, and the count never goes below ten.
Services are never merged to save memory, because a merge mixes rules.

### What counts, and the margin if Q-13 goes against it

| Kind | Services | Count |
|---|---|---|
| Core, writes data it owns (PostgreSQL, MongoDB or Redis) | auth, users, products, categories, stores, customers, customer-preferences, sales, engagement, check-ins | 10 |
| Core, read rule only | stock (it serves availability; its one write path, `api_stock.consume_v1`, runs inside the sale), segments, recommendations | 3 |
| Second wave | campaigns (writes), audit-trail (read rule only) | 2 |

If the instructor counts every row, the stage has 15, or 13 without the
second wave. If the instructor counts only services that write, it has 11, or
exactly 10 without the second wave. The floor holds under both answers, but
under the strict one it holds with no margin. In that case no core writer can
be cut, and campaigns-service is the only margin there is.

### Cut order

1. Drop audit-trail, then campaigns (the second wave). The core thirteen
   remain.
2. Lower memory limits as
   [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
   allows, after measuring.
3. Never remove a core service, and never merge two services into one.
   Removing a core writer breaks the floor under the strict reading of Q-13,
   and a merge mixes two rules in one service.

### Adding a sixteenth service

A new service needs no change to any existing service, and no edit to a
hard-coded list in `deploy/` or `.github/`. These four things are its own:

1. **An ADR** that names its single rule, the RN ids behind it, and why the
   rule is not already part of an existing service.
2. **Its directory,** `services/<name>/`, with everything
   [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
   lists.
3. **Its registry line** in `contracts/registry.yaml`, with one rule, its RN
   ids, its wave and its top-level resources. None of those resources may
   already be registered
   ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
4. **Its section of `sql/01_schema.sql`,** if it holds PostgreSQL data: its
   schema, tables, `api_*` objects, triggers and grants, applied before the
   service ships.

The other records require further lines in shared files: its `svc_*` role in
`sql/00_create_database.sql`, its rows in `contracts/db/grants.csv`, its
contract under `contracts/<name>/`, a local default in `.env.example`, and a
`mem_limit` that keeps the sum within budget. It takes the next free port
after 5015. A producer whose `api_*` object it consumes adds the grant to its
own section.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0016's six to ten, with eight grouped boundaries | Grouped boundaries hold two or three rules each, for example products with categories, or customers with preferences. That breaks the one-rule requirement. Six is also below the instructor's floor |
| Build exactly ten services | Ten leaves no slack. If even one boundary were judged not to be a rule, the stage would fail the minimum. It would also force merges of rules the domain keeps apart |
| Split until every RN id is its own service (thirty or more) | Many RN ids are facets of one invariant. RN-06, RN-07 and RN-33 all protect the category tree. Splitting them would put one table under several writers, which ADR-0034 forbids, and would produce filler services that the instructor excluded |
| Count only services that write, and add services until fifteen of them write | It would need writers for data the domain does not have, such as service-side segmentation runs, which the frozen web owns (RN-05). That is filler under another name. Waiting for Q-13 costs nothing, because the floor holds under the strict reading too |
| Replace ADR-0016 entirely | ADR-0016's reinstatement of the distributed architecture, its four components, and its rule that nothing is built before its record are all still right. Only the count is wrong |

## Consequences

**What this makes easy.**
- Every service can be explained in one sentence, and reviewed against that
  sentence. A pull request that adds a second rule to a service is visibly
  out of scope.
- The count is checked by CI from the registry, not argued in a meeting.
- Growth is additive. A new rule becomes a new service, without editing a
  running one.

**What this makes hard.**
- **Fifteen deployables for a team of students.** That is fifteen
  pipelines, contracts, XSDs, ACL files and NGINX includes on one 8 GB VM.
  Each costs review time whether or not it is interesting.
- **Small services carry fixed overhead.** stores and check-ins each have
  one table and a few endpoints, yet each needs the same container, health
  checks, JSON and XML, and authentication as sales.
- **Rules that span facts are coordinated through the database.** A sale
  consumes stock across two boundaries. The single-rule split forces that
  coupling into a versioned contract
  ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md),
  [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md))
  rather than into one service's transaction code.
- **The definition depends on an answer the team does not have.** Until Q-13
  is answered, no core service can be cut. Under the strict reading, the core
  has exactly ten services that count.

**What must now be true elsewhere.**
- [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)'s
  status line becomes `Partially superseded by ADR-0043 (service count only)`
  when this record is accepted, in the same pull request, and not before. Its
  decision text is unchanged.
- [`docs/adr/README.md`](README.md) records that transition alongside those
  of ADR-0015 and ADR-0020.
- [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md)
  lists Q-13 as an open question: "Does a read-only boundary (segments,
  audit-trail) count as a microservice implementing one business rule?" The
  working assumption is yes, and the answer is recorded in writing.
- `docs/business-rules.md` gains RN ids for the four rules the stage
  introduces: stores' location, customer-preferences' whole-set replace,
  engagement's once-per-event record, and check-ins' geofence verification.
  It must also disambiguate the two headings that share RN-37 (K-means runs
  and accepted sales) before the registry cites one of them.
- Each entry in `contracts/registry.yaml` carries the fields that
  [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)
  defines: `name`, `port`, `rule` (one string), `rn` (its RN ids) and
  `resources`. This record adds one field, `wave` (`core` or `second`).
- `AGENTS.md` replaces "eight service boundaries" with this record's floor and
  the fifteen planned services.

## Compliance

CI runs this from the repository root. It prints nothing and exits 0 when the
registry has at least ten core services, each with exactly one rule, and
matches `services/` one to one:

```bash
python3 - <<'EOF'
import pathlib, sys, yaml

entries = yaml.safe_load(pathlib.Path("contracts/registry.yaml").read_text())["services"]
names = [e["name"] for e in entries]
core = sum(1 for e in entries if e.get("wave") == "core")
not_one_rule = [
    e["name"] for e in entries
    if not isinstance(e.get("rule"), str) or not e["rule"].strip() or not e.get("rn")
]
dirs = sorted(p.name for p in pathlib.Path("services").iterdir() if p.is_dir())

problems = []
if core < 10:
    problems.append(f"{core} core services: the floor is ten without the second wave")
if len(set(names)) != len(names):
    problems.append("a service is registered twice")
if not_one_rule:
    problems.append(f"not exactly one rule: {not_one_rule}")
if sorted(names) != dirs:
    problems.append(f"registry {sorted(names)} does not match services/ {dirs}")
sys.exit("\n".join(problems) or None)
EOF
```

Once the RN ids exist, every `rn` entry must resolve to a heading in
`docs/business-rules.md`:

```bash
python3 - <<'EOF'
import pathlib, re, sys, yaml

known = set(re.findall(r"^### (RN-\d+[a-z]?) ", pathlib.Path("docs/business-rules.md").read_text(), re.M))
entries = yaml.safe_load(pathlib.Path("contracts/registry.yaml").read_text())["services"]
unknown = [(e["name"], r) for e in entries for r in e["rn"] if r not in known]
sys.exit(f"unknown rule references: {unknown}" if unknown else None)
EOF
```

After acceptance, the ADR-0016 status line names this record:

```bash
grep -q '^\*\*Status:\*\* Partially superseded by \[ADR-0043\]' \
  docs/adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md
```
