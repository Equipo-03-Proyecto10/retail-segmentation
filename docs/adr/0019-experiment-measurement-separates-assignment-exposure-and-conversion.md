# ADR-0019 — Experiment measurement separates assignment, exposure and conversion

**Status:** Proposed
**Owner:** Marcelo
**Issue:** —
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0016](0016-the-second-delivery-reinstates-the-distributed-architecture.md)
makes the Flask monolith one component of the second delivery and requires the
web system to keep working independently. These analytics are the monolith's
part of that delivery. They do not restore the first delivery's C-1 or C-2
constraints in [`scope.md`](../scope.md), or decide other components' contracts.

[ADR-0004](0004-model-ahead-of-the-deferred-segmentation-modules.md) kept
`campaign`, `experiment`, `experiment_group` and `experiment_group_customer` in
[`sql/01_schema.sql`](../../sql/01_schema.sql) while forbidding application
code from reading them in the first delivery. This successor puts them to work;
it does not correct or supersede that decision. It fulfils ADR-0004's warning
that deferred analytics would change a model designed before their rules.

The existing tables are not sufficient as they stand. `campaign` already has
the target segment, dates and lifecycle state this decision needs. `experiment`
has dates and a target metric, but no precommitted conversion window or
data-origin marker. `experiment_group.kind` distinguishes control from treatment
but permits zero or several control groups.
`experiment_group_customer.assigned_at` is the required assignment timestamp,
but its key permits the same customer to enter two groups in one experiment.
There is no exposure record and no conversion record.

The `transaction` and `transaction_line` tables are sufficient as the observed
outcome source. They preserve who bought, when, the sale total and the products
and prices involved. A sale needs no experiment column; the missing fact is the
separate attribution from an assignment to a qualifying sale.

Conflating assignment and exposure lets delivery and engagement change the
denominator. A movable window invites a favourable end date, while no
untreated control makes seasonality and general purchasing changes look like
campaign effect.

## Decision

The monolith records group assignment, exposure and conversion as three
distinct durable events for every experiment: it writes an assignment with its
timestamp before any outcome is known, records each later exposure separately
so an assigned customer may remain unexposed, and records conversion by
linking the assignment to a qualifying transaction inside a positive
conversion-window duration fixed before the run starts and immutable after the
first assignment. Every experiment has exactly one control group, at least one
treatment group, and no exposure for the control; the primary comparison uses
every assigned customer in the assigned arm whether exposed or not. The model
adds durable exposure and conversion relations, adds the window and data
origin to the experiment, and changes the assignment key or constraint so one
customer cannot join two arms of the same experiment. Every screen, export and
documentation example derived from seeded or injected data displays an
explicit `Synthetic` label; generated results never appear as observed
business results.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Compare purchases of exposed customers with everyone else | This creates selection bias in the positive direction. Customers who open a message are already more engaged than assigned customers who do not. The comparison measures that prior engagement, attributes it to the campaign and can report a positive effect for a campaign that did nothing |
| Treat assignment and exposure as one event | It removes assigned-but-unexposed customers from the treatment denominator. A delivery failure then looks like no assignment, intent-to-treat uplift cannot be computed, and treatment reach cannot be audited separately from treatment effect |
| Choose the conversion window after inspecting transactions | Trying several end dates and retaining the strongest result inflates the false-positive rate. The reported significance no longer has the stated meaning because the window itself was selected on the outcome |
| Compare purchases before and after a campaign without a control | Seasonality, price changes and an overall change in store traffic affect the two periods. The design has no contemporaneous population with which to separate those changes from campaign effect |

## Consequences

**What this makes easy.** Assignment counts, exposure counts and conversion
counts have stable and intentionally different denominators. The main result is
an intent-to-treat comparison, while exposure rate remains a separate delivery
diagnostic. A conversion can be reproduced from the recorded assignment,
window and qualifying `transaction` rows without adding experiment state to a
sale.

The measurement ships with two executable validations. The A/A validation uses
only pre-cut-off data for assignment and actual `transaction` rows after the
cut-off for conversion. A deterministic split must show no significant
difference at alpha 0.05; a difference means the measurement is broken, not
that the experiment worked. The injected-uplift generator uses a fixed seed.
Its fixture gives 10,000 assignments per arm, 10% control conversion and 15%
treatment conversion; the estimate must recover five percentage points within
0.1 point and its 95% interval must exclude zero.

**What this makes hard.** The current four experiment tables need schema work
before application work. The schema must represent exposure and conversion,
enforce experiment-level assignment uniqueness, reject exposure for a control,
and make activation impossible without one control and one treatment. The
service locks the conversion duration before assignment and distinguishes an
unfinished window from one that ended without conversion. Provenance survives
every chart or export; a renderer cannot infer it from a demonstration mode.

**What must now be true elsewhere.** The schema story updates
[`sql/01_schema.sql`](../../sql/01_schema.sql), its seed, the 4NF argument and
data dictionary in [`data-model.md`](../data-model.md), and the experiment
rules in [`business-rules.md`](../business-rules.md) before the measurement
story reads these tables. The service and data-access work follows
[ADR-0003](0003-layered-architecture-with-an-explicit-service-layer.md) and
[ADR-0014](0014-service-owned-transactions-and-typed-write-failures.md), so the
service owns the atomic assignment and event writes and all SQL remains in
`web/db`. Screen and export stories use the existing profiles and gate from
[ADR-0007](0007-permissions-in-code-with-a-default-deny-middleware.md); this
record creates no role or permission. The future second-delivery scope and
[`roadmap.md`](../roadmap.md) must identify this as the monolith analytics
phase.
Nothing here changes C-4 or either enforcement of the single administrator.

## Compliance

The implementation provides these deterministic tests; a reviewer runs them
from the repository root:

```bash
pytest -q \
  tests/test_experiment_measurement.py \
  tests/test_experiment_measurement_ui.py

# The first file proves assignment precedes outcomes, assigned-unexposed
# customers remain in the denominator, the control has no exposure, one
# customer cannot enter two arms, and the conversion window cannot change
# after assignment. It also runs the seeded A/A and injected +5 point fixtures.
# The UI file renders both HTML and export output from SEEDED and INJECTED
# origins and asserts that each contains the literal label "Synthetic".
```
