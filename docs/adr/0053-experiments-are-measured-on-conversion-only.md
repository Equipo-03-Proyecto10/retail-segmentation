# ADR-0053 — Experiments are measured on conversion only; `AVERAGE_TICKET` is removed

**Status:** Accepted
**Owner:** Max
**Issue:** #457
**Supersedes:** —
**Superseded by:** —

---

## Context

The experiment form offers two target metrics: `CONVERSION` and `AVERAGE_TICKET`,
the latter labelled "not measured yet" (#357). Uplift, the experiment dashboard
and the report refuse an experiment whose metric is not `CONVERSION`, and 15 of
the 30 seeded experiments use `AVERAGE_TICKET`. Each of those is a setup that
can never produce a result.

ADR-0019 makes the primary comparison intent-to-treat, over every assigned
customer whether exposed or not. Average ticket, meaning spend per purchase,
exists only for customers who bought. Comparing it between arms conditions on a
post-assignment outcome. If a treatment brings in extra buyers who spend less,
the treatment arm's average ticket falls even when the treatment raised revenue.
That is the selection bias ADR-0019 rejected for exposure, so measuring the
metric as named is not an option.

A metric that respects ADR-0019 does exist: revenue per assigned customer, zero
for those who did not buy. It needs a different estimator, a difference in means
with a Welch interval instead of a difference in proportions, as well as its
own rule, tests and validation fixtures. No story asks for it.

## Decision

`CONVERSION` is the only target metric. `AVERAGE_TICKET` is removed from the
service's vocabulary, so the form no longer offers it. The seed gives every
experiment `CONVERSION`, and the instance's seeded experiments are corrected by
a documented statement. Uplift, the dashboard and the report keep refusing any
other stored value, so a row that predates the correction still shows a reason
instead of a figure. A revenue metric, if wanted, arrives as a new story
measuring revenue per assigned customer, under its own rule.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Measure average ticket as spend per purchase among buyers | It conditions on the outcome, so it is biased by who buys rather than by what they spend, which is the comparison ADR-0019 rejected |
| Replace it now with revenue per assigned customer | It is valid, but it is a new estimator with its own validation fixtures. No requirement asks for it, and the roadmap rule is not to build deferred work early |
| Keep offering it as "not measured yet" | Half the seeded experiments, and any new one that picks it, are setups that can never produce a result |
| Add `CHECK (target_metric = 'CONVERSION')` to the schema now | It is a schema change outside this decision's scope, and the one writer that matters, the seed, is checked by CI. The vocabulary stays the service's, as `web/services/experiments.py` already documents |

## Consequences

**What this makes easy.** Every experiment that can be created can be measured,
and the demonstration has no experiment whose result page is a refusal by design.

**What this makes hard.** An experiment with any other stored metric still opens
but cannot be renamed until its metric is corrected, because the edit form
validates the stored value. Only a database loaded before this change holds
such a row, and the statement in `deploy/README.md` corrects it.

**What must now be true elsewhere.** HU-24, the experiments section and the
limitations list of `docs/demo-system-guide.md`, and the `experiment_conversion`
entry in `docs/data-model.md` stop describing `AVERAGE_TICKET` as available.

## Compliance

- `tests/test_experiment_frame.py` asserts that `CONVERSION` is the only target
  metric. `tests/test_experiments.py` asserts that a form submitting
  `AVERAGE_TICKET` is refused.
- CI's seed compliance block fails if any experiment's `target_metric` is not
  `CONVERSION`.
