# ADR-0043 — The distributed stage has at least ten microservices, each implementing one business rule

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md), in part. Only its count of six to ten services
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md) sized the
distributed stage at six to ten services, and the earlier drafts built on that. The instructor has
since set four binding requirements, which the team confirmed:

1. **Total independence.** Each service is built, versioned, deployed, scaled and rolled back on its own.
2. **Never mix endpoints.** No service exposes, duplicates, proxies or nests another's resources.
3. **One business rule per microservice,** never two.
4. **At least ten microservices,** with no upper cap, and no filler.

The fourth turns ten into a floor; the third makes the count follow the domain. None defines "one
business rule". `docs/business-rules.md` gives every enforced invariant an RN id, but segments and
audit-trail enforce no write invariant: they only read and report. Whether the instructor counts
such a read-only boundary was the genuine uncertainty, asked as **Q-13**; the instructor confirmed on
2026-10-15 that it counts. This record still keeps a margin that survives either answer.

## Decision

The distributed stage has **at least ten microservices and no upper cap**. Each implements exactly
one business rule and owns its resources exclusively. The team plans **fifteen**: thirteen core
services and two in a second wave (campaigns and audit-trail), registered in
[ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md). A
**business rule** is one invariant that a service enforces on data it writes, or one read rule that
governs what it may report and to whom. It is stated in one sentence and backed by RN ids in
`docs/business-rules.md`, where this pull request adds RN-52 to RN-56. One invariant may govern
several facts in the same way: customer-preferences replaces each of its two preference sets only as
a whole. Facts governed by different invariants are different rules, and so different services:
products and categories; customers and customer-preferences. A service may also serve, read-only, a
vocabulary its own rule validates against, as sales serves `channels` and users serves `roles`. Read-only boundaries count,
as the instructor confirmed (Q-13). If memory runs short, the second wave is dropped first, and the count never goes below
ten. Services are never merged to save memory, because a merge mixes rules.

### What counts, and the margin if Q-13 goes against it

| Kind | Services | Count |
|---|---|---|
| Core, writes data it owns (PostgreSQL, MongoDB or Redis) | auth, users, products, categories, stores, customers, customer-preferences, sales, engagement, check-ins | 10 |
| Core, read rule only | stock (it serves availability; its one write path, `api_stock.consume_v1`, runs inside the sale), segments, recommendations | 3 |
| Second wave | campaigns (writes), audit-trail (read rule only) | 2 |

If the instructor counts every row, the stage has 15, or 13 without the second wave. If it counts
only services that write, it has 11, or exactly 10 without the second wave. The floor holds under
both answers, but under the strict one with no margin: no core writer can be cut, and
campaigns-service is the only margin there is.

### Cut order

1. Drop audit-trail, then campaigns (the second wave); the core thirteen remain.
2. Lower memory limits as
   [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)
   allows, after measuring.
3. Never remove a core service (it breaks the floor under the strict reading of
   Q-13), and never merge two (it mixes two rules).

### Adding a sixteenth service

A sixteenth service needs its own ADR naming its single rule and RN ids, its `services/<name>/`
directory, its registry line with the next free port after 5015, and, if it holds PostgreSQL data,
its idempotent section of `sql/01_schema.sql`. It also adds lines to shared files: its role in
`sql/00_create_database.sql`, its rows in `contracts/db/grants.csv`, its contract under
`contracts/<name>/`, a default in `.env.example`, and a `mem_limit` within the budget. Nothing in
`deploy/` or `.github/` lists services ([ADR-0034](0034-fifteen-single-rule-services-each-the-only-service-writer-of-its-data.md), [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md), [ADR-0040](0040-each-service-is-built-in-ci-and-runs-as-its-own-compose-project.md)).

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep ADR-0016's six to ten, with eight grouped boundaries | Grouped boundaries hold two or three rules each, for example products with categories, or customers with preferences. That breaks the one-rule requirement. Six is also below the instructor's floor |
| Build exactly ten services | Ten leaves no slack. If even one boundary were judged not to be a rule, the stage would fail the minimum. It would also force merges of rules the domain keeps apart |
| Split until every RN id is its own service (thirty or more) | Many RN ids are facets of one invariant. RN-06, RN-07 and RN-33 all protect the category tree. Splitting them would put one table under several writers, which ADR-0034 forbids, and would produce filler services that the instructor excluded |
| Count only services that write, and add services until fifteen of them write | It would need writers for data the domain does not have, such as service-side segmentation runs, which the frozen web owns (RN-05). That is filler under another name. Q-13 has since confirmed that read-only boundaries count, and the floor holds under the strict reading too |
| Replace ADR-0016 entirely | ADR-0016's reinstatement of the distributed architecture, its four components, and its rule that nothing is built before its record are all still right. Only the count is wrong |

## Consequences

**What this makes easy.**
- Every service is explained, and reviewed, against one sentence; a pull request that adds a
  second rule to a service is visibly out of scope.
- The count is checked by CI from the registry, not argued in a meeting.
- Growth is additive: a new rule becomes a new service.

**What this makes hard.**
- **Fifteen deployables for a team of students:** fifteen pipelines, contracts, XSDs, ACL files
  and NGINX includes on one 8 GB VM.
- **Small services carry fixed overhead.** stores and check-ins each have one table, yet need the
  same container, health checks, JSON, XML and auth as sales.
- **Rules that span facts are coordinated through the database.** A sale consumes stock across two
  boundaries, through a versioned contract ([ADR-0039](0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md),
  [ADR-0044](0044-boundaries-meet-only-through-versioned-database-contracts-and-redis-streams.md)).
- **The definition rested on Q-13,** which the instructor confirmed on 2026-10-15. No core service is cut
  anyway: under the strict reading the core has exactly ten that count, and the margin stays.

**What must now be true elsewhere.**
- [ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)'s status line
  becomes `Partially superseded by ADR-0043 (service count only)` on acceptance, in the same pull
  request; its decision text is unchanged. [`docs/adr/README.md`](README.md) records it with
  ADR-0015 and ADR-0020.
- [`scope-delivery-2-distributed.md`](../scope-delivery-2-distributed.md) records Q-13's answer in §11:
  a read-only boundary counts, and the shared-database model counts as independent.
- `docs/business-rules.md` gains RN-52 (auth), RN-53 (stores), RN-54 (customer-preferences), RN-55
  (engagement) and RN-56 (check-ins) in this pull request. The duplicate RN-37, RN-47 and RN-50
  headings are left to AN-15 (#460). The registry cites the sales rule as RN-37 (accepted sales),
  and `rn` holds plain ids.
- Each `contracts/registry.yaml` entry carries `name`, `port`, `rule`, `rn`, `wave` and
  `resources`; [ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)
  defines the format, `wave` included.

## Compliance

CI runs this from the repository root. It prints nothing and exits 0 when the registry has at
least ten core services, each with exactly one rule, and every `services/<dir>` is registered. An
entry may precede its directory, because the contract merges first:

```bash
python3 - <<'EOF'
import pathlib, sys, yaml
entries = yaml.safe_load(pathlib.Path("contracts/registry.yaml").read_text())["services"]
names = [e["name"] for e in entries]
core = sum(1 for e in entries if e.get("wave") == "core")
not_one_rule = [e["name"] for e in entries
                if not isinstance(e.get("rule"), str) or not e["rule"].strip() or not e.get("rn")]
dirs = sorted(p.name for p in pathlib.Path("services").glob("*") if p.is_dir())
problems = []
if core < 10:
    problems.append(f"{core} core services: the floor is ten without the second wave")
if len(set(names)) != len(names):
    problems.append("a service is registered twice")
if not_one_rule:
    problems.append(f"not exactly one rule: {not_one_rule}")
unregistered = [d for d in dirs if d not in names]
if unregistered:
    problems.append(f"services/ without a registry entry: {unregistered}")
sys.exit("\n".join(problems) or None)
EOF
```

Every `rn` entry must resolve to a heading in `docs/business-rules.md`:

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
grep -qF '**Status:** Partially superseded by ADR-0043 (service count only)' docs/adr/0016-the-second-delivery-reinstates-the-distributed-architecture.md
```
