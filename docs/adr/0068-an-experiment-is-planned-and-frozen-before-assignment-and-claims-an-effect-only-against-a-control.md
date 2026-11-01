# ADR-0068 — An experiment is planned and frozen before assignment, and claims an effect only against a control

**Status:** Proposed
**Owner:** Max
**Issue:** #832
**Supersedes:** in part [ADR-0028](0028-experiment-treatment-arms-may-omit-control.md) (that a no-control experiment may report any effect) and in part [ADR-0053](0053-experiments-are-measured-on-conversion-only.md) (that conversion is the only target metric, and that a revenue metric waits for a later story). Both take effect on this record's acceptance, not before.
**Superseded by:** —

---

## Context

The first delivery built an experiment pipeline in the web system: assignment,
exposure and conversion are three append-only facts (ADR-0019, ADR-0026,
ADR-0027, ADR-0050), the primary comparison is intent-to-treat (RN-48), and a
95% interval comes from a two-proportion comparison (`compare_proportions` in
`web/services/experiment_uplift.py`), validated by an A/A split and an
injected-effect fixture. The third delivery moves experimentation into
experimentation-service (port 5020, ADR-0059), and the instructor requires
commercially verifiable results (`docs/scope-delivery-3.md` section 1, and
section 10: no treated group's sales presented as revenue the campaign caused).
Read against that bar, six things are missing or wrong. This record fixes the
experiment's rules before anyone reads a result.

1. **Assignment cannot be verified, and is fragile.** `split` in
   `web/services/experiments.py` shuffles the sorted customers with
   `random.Random(f"experiment-{experiment_id}")` and deals them round-robin.
   The seed is the experiment id, a public and guessable integer, so nothing
   stops a team from choosing which experiment id, or which moment, gives a
   favourable split. The permutation depends on the whole list: one more
   customer holding the label at that second reshuffles everyone. A third party
   cannot reproduce it without the exact list of customers who held the label at
   that instant, and that list is stored nowhere: the label is read live and
   `experiment_assignment` records only the outcome.
2. **A control is optional (ADR-0028).** A two-treatment experiment is a valid
   comparison of two offers, but it holds no untreated population, so it cannot
   separate a campaign's effect from seasonality, price changes or a general
   rise in traffic, the argument ADR-0019 already made against before-and-after
   comparison.
3. **No sample size is planned.** `experiment` holds a metric, dates and a
   window; nothing records a minimum detectable effect, alpha or power. With 30
   seeded customers no seeded experiment could detect any plausible effect, and
   the page does not say so. An underpowered "no significant difference" is then
   read as "no effect".
4. **The population is the label, nothing else.** `_plan` assigns every customer
   holding the campaign's label. Consent and restrictions did not exist when it
   was written; they are being built now (customer-preferences, F21-17), and a
   customer who refuses marketing must not be assigned for delivery. A customer
   can be assigned in two running experiments, and one sale can then convert for
   both (RT-08).
5. **Looks are unbounded.** The result page recomputes whenever it is opened.
   Nothing fixes when the result counts, so a team can stop when the interval
   happens to exclude zero, which inflates the false-positive rate well above
   the stated 5%. ADR-0019 rejected the same flexibility for the conversion
   window.
6. **ADR-0053 left a hole.** It removed average ticket because spend per purchase
   conditions on buying, and named revenue per assigned customer as the valid
   replacement but deferred it. The delivery now needs results in money, so the
   deferral ends here.

What is genuinely uncertain, and a judgement call: the default alpha and power
are conventions, not facts about this business; baseline conversion comes from
the customers' own history and may be poorly estimated on a small instance; the
7-day settle period and the thresholds of the two checks are chosen, not derived;
and a hash-based assignment is verifiable but cannot prove that the operator did
not discard earlier salts (see Consequences).

The boundary with the attribution ADR (#833): this record decides how customers
are assigned and how the effect of an assigned arm is estimated. The attribution
ADR decides which campaign receives credit for a sale, counted once, and keeps
that apart from effect. Nothing here gives credit to a sale.

## Decision

An experiment is a frozen plan. Before the first customer is assigned,
experimentation-service writes, in one transaction and immutably (extending
RN-49), the **plan**: the frame (the eligible customers), the primary metric, the
estimator, alpha, power, the minimum detectable effect, the baseline, the
required customers per arm, the allocation, the conversion window `W`, the
analysis instant `A`, and a salt. Assignment is a pure function of that plan,
anyone can recompute it, and the result is read once, at `A`. The rules below
bind, and their numbers are the defaults a reviewer can check.

**1. Eligible population and the frame.** The frame is the set of customers that
satisfy all of the following **at one recorded instant `T`**, taken from the
PostgreSQL clock in the transaction that draws the salt, not from the web
server (as RN-49 does for dates):
- they hold the campaign's target label under the segmentation model version
  published at `T` (ADR-0017 history, ADR-0063, ADR-0064);
- they have a recorded consent for the campaign's channel and no restriction
  covering it, as customer-preferences records it at `T`;
- they are not anonymized, not inactive, and not in the campaign's exclusions
  (the audience and exclusion rules campaigns-service publishes, F25-03 and
  F25-04);
- they are not in the frame of another experiment whose period overlaps this
  one's (rule 6).

The frame is stored, not recomputed: one row per customer, a digest (SHA-256
over the sorted customer ids and the criteria as canonical JSON), and the count
removed by each exclusion reason. Every criterion is decided at `T`, before
randomization, so exclusions apply identically to all arms and do not bias the
comparison. Consent withdrawn, or an anonymization, after assignment stops
further exposure and use of personal data, but the customer stays in the
assigned count and the analysis (as an identifier that may be pseudonymous),
because dropping them would condition on a post-assignment event.

**2. Randomization a third party can verify.** A customer's assignment key is
`HMAC-SHA-256(key = salt, message = "mosaiq-assign-v1|" + experiment_id + "|" +
customer_id)`. The salt is 32 bytes from the operating system's CSPRNG, drawn
once in the plan's transaction after the frame is fixed, and published with the
plan. It is not a secret: the HMAC gives separation between experiments and
makes the draw independent of anything chosen in advance, not confidentiality.
The frame is ordered by `(key, customer_id)` ascending and dealt round-robin to
the arms, control first and then treatments by group id. Arm sizes therefore
differ by at most one (RF-24, ADR-0028) under equal allocation; unequal
allocation is out of scope until an ADR asks for it. A verifier needs only the
stored frame, the salt, the experiment id, the arm order and this paragraph. The
service ships a verifier without a database dependency, pinned by golden
fixtures in the manner of ADR-0045, and "recomputation equals the stored
assignment" is one of the plan's integrity checks. Assignment is written once
and never rewritten (RN-42). A customer who later acquires the label is never
added; the stored frame is the only record of who was eligible.

**3. Control, and what may be claimed.** No statement of effect, incremental
effect, uplift, lift or revenue caused by a campaign is made about an experiment
without a control arm, in any page, export, API field or report. A
campaign-effect experiment has exactly one control and one or more treatments.
ADR-0028's two-treatment experiment stays creatable and assignable as a
**comparison of two offers**: it may state the difference between the arms and
its interval, labelled "difference between treatments", and never an effect of
the campaign against no treatment. This supersedes ADR-0028 only in the claim it
allowed; its arm structure, immutability and even split stand.

**4. Sample size before assignment.** The plan sets, for the primary metric:
alpha 0.05 two-sided (family-wise: with `m` treatment arms each is compared with
the control at `alpha' = 0.05 / m`, Bonferroni); power 0.80; the **minimum
detectable effect** (MDE) as an absolute difference in the intent-to-treat
metric; and the baseline, estimated from the frame's own net sales over the
period of equal length before `T`. For conversion the customers per arm are
`n = ceil( (z(1 - alpha'/2) + z(1 - beta))^2 * (p1 (1 - p1) + p2 (1 - p2)) /
(p2 - p1)^2 )` with `p2 = p1 + MDE`; baseline 10%, MDE 2 points and `m = 1` give
3,839 per arm. For revenue per assigned customer,
`n = ceil( 2 * (z(1 - alpha'/2) + z(1 - beta))^2 * s^2 / MDE^2 )`, with `s` the
standard deviation of the same metric in the pre-period (zero-inflated and
skewed, so the formula is an approximation that the validation in Compliance
checks). The MDE is in intent-to-treat terms: if only part of the treatment arm
is reached, the effect among the reached is diluted by that share, and the MDE is
chosen with that in mind. If the frame divided by the number of arms is below
`n`, the plan is stored with the flag **UNDERPOWERED** and the smallest effect
the frame can detect; assignment then needs an explicit recorded
acknowledgement, and every result of that experiment carries the flag. The
30-customer seed is expected to be flagged. A non-significant result of an
underpowered experiment is reported as "inconclusive", never as "no effect".

**5. Intent-to-treat is the primary analysis; no counterfactual exposure.** The
primary analysis keeps every assigned customer in the assigned arm, exposed or
not (ADR-0019, RN-48). The control needs **no** recorded "would have been
exposed": ADR-0027 stays and the control is never exposed. The per-exposure rate
remains a descriptive secondary measure, labelled non-causal, because the
exposed are a self-selected part of their arm and the control has nothing
comparable. An effect on the exposed (a complier effect) would need a recorded
counterfactual exposure for the control; it is not built, and is deferred to the
roadmap.

**6. Contamination.** A customer is assigned in at most one experiment whose
period overlaps. An experiment's period runs from its start to its analysis
instant `A`. A later experiment's frame excludes any customer in the frame of an
earlier experiment with an overlapping period, and counts them under that
exclusion reason; the first experiment planned keeps its customers. Assigned sets
of overlapping experiments are then disjoint, so a customer's sale can belong to
the conversions of at most one experiment through assignment. Which campaign
receives credit beyond that is the attribution ADR's decision.

**7. Sample ratio and balance checks.** The service runs and stores two checks.
(a) **Sample ratio check**: a chi-square goodness-of-fit test of the counts that
reach the analysis, per arm, against the planned allocation. `p < 0.001` marks
the result **INVALID** and withholds the estimate. The deal is exact, so assigned
counts cannot differ by more than one; the check therefore detects pipeline
faults (assignments missing from the frame, customers lost at analysis, arms
analysed unevenly), not randomization. (b) **Balance check** on pre-`T` net spend
and purchase count: the standardized mean difference per arm, flagged as a
diagnostic above 0.25. Balance never changes the estimate and is never used to
re-draw an assignment.

**8. Primary metric, estimator and horizon.** An experiment declares exactly one
primary metric before assignment: `CONVERSION` or `REVENUE_PER_CUSTOMER`
(superseding ADR-0053 where it limits the metric to conversion; `AVERAGE_TICKET`
stays removed). The other is reported as a secondary, with its interval and no
claim. Both are measured over `[t, t + W)` from each customer's assignment
instant `t`, counting only sales that remain after reversals (ADR-0062, net
units) as known at `A`.
- `CONVERSION`: the difference of proportions, the unpooled Wald interval at
  `1 - alpha'` and the pooled two-sided test, as `compare_proportions` does today.
- `REVENUE_PER_CUSTOMER`: `Y_i` is the net sales total of customer `i` in the
  window, **0 if none**, over **every assigned customer**, not only buyers. The
  estimator is the difference of arm means with a Welch (unequal variance)
  interval at `1 - alpha'`, Satterthwaite degrees of freedom, and the two-sided
  Welch test. A seeded percentile bootstrap (10,000 resamples) is computed
  beside it as a check and must agree on the sign and on whether the interval
  excludes zero; a disagreement is reported with the result.

The **analysis horizon is fixed in the plan**. There is one primary look, at
`A = t + W + 7 days`, the seven days being a settle period for late sales and
reversals. Before `A` the page shows counts and the elapsed share of the window,
and no interval, p-value or direction. At `A` the result is computed once and
stored. A later read is a labelled re-read and does not replace the primary
result. Stopping early, extending the window or adding a look requires a new
experiment.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep `random.Random(f"experiment-{id}")` and only store the frame | The seed is a public, guessable integer, and the whole permutation depends on the list. Reproducing it also requires Python's Mersenne Twister and its string seeding, so any other-language verifier depends on an implementation detail rather than a specification. An HMAC-SHA-256 of a salt and an id is a standard recomputable anywhere |
| Hash each customer alone and cut by threshold (`key mod k`) | It is stable for a late entrant, but arm sizes become binomial rather than within one, which breaks RF-24 and ADR-0028 and costs power at small n; and admitting a late entrant is what the stored frame forbids, to keep the denominator fixed |
| Hash the customer id with no salt, or one salt for all experiments | The same customers would land in the same arm in every experiment, correlating results across experiments and letting anyone predict an assignment before it is made |
| Stratified or blocked randomization on segment, store or pre-period spend | It lowers variance, but adds covariates the verifier and contract must freeze; with one label per campaign the frame is already homogeneous in segment. Deferred until the balance check shows a need, by a new ADR |
| Keep a no-control experiment as a valid way to report an effect | Without an untreated population seasonality, price changes and traffic are inseparable from the campaign (ADR-0019). ADR-0028 itself refused an uplift for this reason; the claim restriction applies the same logic to every phrasing of effect |
| Record a "would have been exposed" for the control now | It would make an effect on the exposed estimable, but needs a decision point in the floor flow that does not exist, a new fact in the engagement taxonomy and a second estimator. No acceptance test asks for it; ITT answers whether assigning a campaign pays |
| Only warn on, or refuse, an underpowered experiment | A warning that is not stored is lost on export. A refusal would make the 30-row demonstration data unable to show an experiment at all. A stored flag with a recorded acknowledgement keeps both honest |
| Sequential testing with alpha spending, to allow looks | The right tool when early stopping is wanted, but it needs its own boundaries, interval and validation, and nothing in the delivery needs early stopping. One fixed look is simple to state and to test |
| Average ticket among buyers, or revenue among converters | Both condition on a post-assignment outcome, the selection bias ADR-0053 rejected; revenue per assigned customer with zeros does not |
| A rank test (Mann-Whitney) for revenue | It tests stochastic dominance, not a difference in money; the commercial question is the difference in mean revenue, which Welch and the bootstrap estimate |
| Re-draw the assignment when the balance check flags | It is selection on pre-period data after seeing the draw, which voids the randomization and the stated error rates |

## Consequences

**What this makes easy.** Anyone holding the plan can recompute every assignment
and see that nothing was chosen after seeing results. A claim of effect is
limited to designs that can support it. A reader of any result knows its power,
its MDE and whether it was underpowered, and with one look a p-value means what
it says. Revenue effect becomes a defined, validated number.

**What this makes hard.** The plan needs a stored frame, a salt, a digest and the
plan fields: a schema and a contract owned by experimentation-service; this record
changes no schema. Experiments assigned under the old rule have no frame or salt:
they stay readable but are marked "assignment rule v0", cannot be recomputed and
are not called verified. The hash gives verifiability, not proof against the
operator: if the service drew many salts and kept the best, the record would not
show it. This is mitigated by drawing the salt once, inside the plan's
transaction, in reviewed code, and is stated as a residual risk. A single look at
`A` means waiting for the window plus seven days; a team wanting an earlier
answer plans a shorter window. The ITT MDE is larger than the effect among the
reached, so plans need more customers than a per-exposure estimate would.
Consent and exclusion criteria shrink the frame, sometimes below `n`. The
Bonferroni correction costs power for each treatment added.

**What must now be true elsewhere.**
- experimentation-service's contract (#837) carries the plan, frame, salt, flags
  and results in JSON and XML, with the estimator and its fields, and merges
  before code (ADR-0035).
- campaigns-service (#834, #835) publishes the audience, consent-aware
  eligibility and exclusions as a versioned view the frame reads at `T`, and owns
  neither the frame nor assignment.
- customer-preferences (F21-17) exposes consent and restrictions as of an instant.
- experimentation-service takes ownership of the experiment tables (#838). The
  web's `split` and seed are not changed here; whether the web adopts the new
  rule, with the golden fixtures ADR-0045 asks for, is decided in #839.
- RN-23, RN-24, RN-27, RN-48 and RN-49 in `docs/business-rules.md`, RF-23 to
  RF-25 in `docs/requirements.md` and the experiment sections of
  `docs/demo-system-guide.md` are updated by the implementing stories; this
  record edits none of them.
- `SEEDED` and `INJECTED` experiments keep their `Synthetic` label (ADR-0019).

**Unblocks.** #837 (the contract) directly, and through it #838 (service stands
up), #839 (stable, verifiable assignment and balance), #840 (sample size before
assignment), #842 (conversions in their window, net of reversals), #844
(incremental effect with uncertainty, revenue included) and #845 (validation).
#834 and #835 (audiences, eligibility, exclusions) take their input criteria from
rule 1. #841 (real exposures) inherits rule 5. #843 (attribution) is decided by
the attribution ADR and must not contradict rule 6.

## Compliance

Each item is a test or check the implementing stories provide; a reviewer runs
them.
- **Verification.** A golden fixture of frame, salt and arm order with the
  expected assignment, reproduced by an independent implementation and by the
  service; a test that recomputes every stored assignment from the plan and fails
  on any difference; a test that adding a customer to a copy of the frame leaves
  the other customers' keys, and their relative order, unchanged.
- **Size balance.** Arms differ by at most one for 1 to 10,000 customers and 2 to
  5 arms.
- **Control rule.** A test that no API, page or export of an experiment without a
  control contains an effect, uplift or incremental-revenue field or wording, and
  that the two-offer comparison is labelled as such.
- **Power.** The sample-size function matches hand-computed values (including the
  3,839 example); an experiment whose frame divided by its arms is below `n` is
  stored `UNDERPOWERED` and refuses assignment without acknowledgement.
- **Frame.** Tests that each criterion excludes the right customers at `T`, that
  consent withdrawn after assignment leaves the customer counted, and that two
  overlapping experiments never share a customer.
- **Validity.** An A/A run whose rejection rate over at least 1,000 seeded
  replications lies in [0.03, 0.07] for both metrics; an injected effect
  recovered within its interval, with interval coverage between 0.93 and 0.97 over
  2,000 replications on a zero-inflated, skewed revenue fixture; a hand-computed
  Welch interval on a six-customer fixture; and the bootstrap agreeing with Welch
  on those fixtures.
- **Single look.** Before `A` no interval, p-value or direction is returned; at
  `A` the result is stored once; a second read does not overwrite it.
- **Sample ratio.** A fixture with 5% of one arm's customers dropped at analysis
  is marked `INVALID`; a clean fixture is not.
