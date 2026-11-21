# ADR-0086 — A verification compares a profile as the incremental design promises it, at one instant

**Status:** Accepted (2026-11-21)
**Owner:** Estefanía
**Issue:** #782
**Supersedes:** —
**Superseded by:** —

---

## Context

ADR-0063 says a verification recomputes everyone and that "equivalent" means raw R, F and M and the label held incrementally equal those of a recomputation from net purchases, with the same version at the same instant. Taken to the letter, that cannot hold for any profile the design is working correctly for: a profile is not rewritten while nothing about the customer moves, so `r_days` (measured to `computed_at`) is days behind at any later instant, and a profile whose `next_change_at` has passed is waiting for the daily pass and legitimately differs from a recomputation made now. A verification that reported these would be wrong every week and its alert would be ignored. It must still find what the design promises not to happen: a sale or a reversal whose event was lost, a customer the pass skipped, a customer with purchases and no profile.

What is uncertain is where to draw the line between lag the design allows and a defect. This record draws it where `next_change_at` and the pass already draw it, and adds no state.

## Decision

A verification recomputes every customer with a profile or a net purchase in the window, under one rule set at one instant `as_of`, from one repeatable-read snapshot, and writes no profile. It compares state, F, M, the three scores and the label as they stand at `as_of`. It compares R measured to the profile's own `computed_at`, because R ages by itself and is not rewritten for it. A profile that fell due after the last completed pass is not compared (the pass owns it); one that fell due at or before it is, because the pass should have moved it. Before any pass has completed, every due profile is the pass's. A customer with purchases and no profile is `pending` and differs once a pass has completed, because the daily pass gives a profile to every customer with a net purchase in the window and none (a seeded customer, a first sale whose event was lost), and an event gives one to a customer who bought since. A profile held under another rule set than the verification's is not compared, since it was scored under the rules in force when it was written, and neither is one computed after `as_of`, which counts what came after the instant. With no rule set named, the verification uses the rule set in force (ADR-0087).

One verification runs at a time. Whoever starts one takes advisory lock `299001` on a session of its own before recording it `RUNNING` and holds it to the end, so a `RUNNING` row whose lock is free was left by a process that stopped. The next start, or the next instance to begin, marks it `FAILED` (`interrupted`), audits it and alerts. Differences are written in the transaction that completes the verification, so a failed one keeps none. A weekly start that fails is retried with a backoff, as the daily pass is. The outcome is `EQUIVALENT` exactly when a completed verification found no difference. A scheduled verification that finds a difference, or fails, raises an alert (an ERROR line, `rfm_verification_differences` or `rfm_verification_failed`) and every verification is audited as a `CALCULATION_RUN` of the RFM calculation.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Compare every value as stored with a recomputation made now | Every aged profile differs in `r_days` and every profile due before the next pass differs in the rest, so the alert fires each week on a correct system |
| Recompute each customer at its own `computed_at` | A sale made after the profile was computed, whose event was lost, is excluded by the recomputation and never found, which is the defect a verification exists to find |
| Refresh every profile to `as_of` before comparing | It writes profiles, which a verification must not do, and it makes the incremental result equal by construction |
| Store the instant of the latest purchase in the profile so R can be compared exactly | A schema change to F22-04's table for a comparison the profile's own `computed_at` and the recomputation's latest purchase already allow |

## Consequences

**What this makes easy.** A difference is a defect: a lost event, a skipped customer or a profile never written. The alert can be trusted, and each difference carries both sets of values for the customer.

**What this makes hard.** A customer that fell due after the last pass and then went wrong in some other way is not looked at until the next pass has run, so a verification right after the pass is the strongest. A sale committed a moment before the instant and not yet consumed shows as a difference until its event is handled; the scheduled run is placed at 03:00 UTC, off the shop's hours and an hour after the pass.

**What must now be true elsewhere.** `rfm.recalculation` and `rfm.recalculation_difference` are written only by rfm-engine: a recalculation is inserted `RUNNING`, a difference only for a running one, and both are final once finished. The daily pass (F22-07) also reads the customers with a net purchase in the window and no profile. The weekly run is configured by `VERIFICATION_ENABLED`, `VERIFICATION_WEEKDAY` and `VERIFICATION_AT_UTC`.

## Compliance

`services/rfm-engine/tests/test_verification.py`: a scripted history of sales, reversals, late sales and duplicate deliveries has zero differences and names the customer if one appears; a lost sale event, a lost reversal event, a missing profile and a profile the pass should have moved are each found; a profile due after the last pass, a profile under another rule set and every pass-owned customer before a first pass are left alone; a reap never ends a verification that is starting, and audits and alerts one that was left; a failed verification keeps no difference; a scheduled difference logs the alert and writes the audit event. `services/rfm-engine/tests/test_daily_pass.py` gives a customer with purchases and no profile their first one.
