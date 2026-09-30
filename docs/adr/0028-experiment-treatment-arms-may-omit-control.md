# ADR-0028 — Experiment treatment arms may omit a control

**Status:** Accepted
**Owner:** Team 03
**Issue:** #342
**Supersedes:** affected parts of ADR-0019 only (the exactly-one-control and
control-required assignment decisions); assignment, exposure, conversion,
append-only, and intent-to-treat decisions remain in force.

## Context

The first experiment design required exactly one control and at least one
treatment. Product experiments also need a two-treatment-arm comparison where
two user-defined offers are split evenly and no untreated population exists.
Group definitions must be visible and stable: a name and treatment description
are user input, shown on every experiment screen and export, and cannot change
after assignment. A no-control experiment cannot produce the existing
control-relative uplift statistic.

## Decision

An experiment has at least one treatment arm and may have zero or one control
arm. Existing controlled experiments retain the exactly-one-control behavior.
A no-control experiment must have exactly two treatment arms; assignment uses
the same deterministic random shuffle and round-robin allocation, producing a
50/50 split (differing by at most one when the population is odd). Assignment
is refused before any write when the population is smaller than the number of
arms.

Each arm stores a user-supplied name and treatment description. These
definitions are immutable once the first assignment exists. Exposure remains
treatment-only and append-only; selected customers or every customer in an arm
may be recorded in one transaction. Uplift refuses no-control experiments with
the precise message that a control-relative uplift cannot be computed.

## Consequences

Controlled experiments continue to use intent-to-treat comparisons against the
control. No-control experiments still support assignment, arm pages, exposure
events, conversion attribution, and report/export counts, but do not fabricate
an uplift against “everyone else.” The schema's partial unique index still
prevents more than one control arm; absence of a control is now valid.

The 4NF decomposition is unchanged: arm descriptions belong to
`experiment_group`, assignment remains a relationship, and exposure remains a
separate event. Seed rows provide meaningful arm metadata.
