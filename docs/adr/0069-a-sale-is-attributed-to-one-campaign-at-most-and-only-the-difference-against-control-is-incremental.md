# ADR-0069 — A sale is attributed to one campaign at most, and only the difference against control is called incremental

**Status:** Proposed
**Owner:** Max
**Issue:** #833
**Supersedes:** —
**Superseded by:** —

---

## Context

Two numbers are about to be built that look alike and mean different things. One
is how much revenue a campaign's customers brought in. The other is how much
more they brought in than they would have without the campaign. Reporting the
first as the second tells a marketer that the campaign caused every sale its
treated customers made, including the ones they would have made anyway.

The repository already holds the pieces, and one gap between them:

- `experiment_conversion` is `UNIQUE (assignment_id, transaction_id)`
  (`sql/01_schema.sql`). RN-23 gives a customer at most one assignment per
  *experiment*, not across experiments, so one customer can be assigned in two
  experiments and one sale can be a conversion of both. Nothing says which
  campaign owns that sale, so two campaigns' totals can add up to more than the
  business sold. Delivery 3 puts conversion-attribution inside experimentation
  (`docs/scope-delivery-3.md`, capability 21), and F25-12 needs a rule before it
  can write a table that refuses a second holder.
- ADR-0019 fixes the conversion window before the run starts, makes it immutable
  after the first assignment (RN-25, `conversion_window_days`), and makes the
  primary comparison intent to treat: every assigned customer, exposed or not.
  Exposure is a separate event, recorded for treatment only (ADR-0027).
- ADR-0050 makes conversions append-only for `retail_app`.
- ADR-0053 leaves `CONVERSION` as the only stored target metric, and names
  revenue per assigned customer, zeros included, as the valid revenue metric
  when one is wanted.
- ADR-0062 (F21-01) makes a reversal a recorded fact that never edits a sale,
  and has every reader of sales count net units
  (`quantity - returned_quantity`). It leaves the effect on conversion and
  attribution to this record.

What is genuinely uncertain. The attribution rule is a judgement call: any rule
is arbitrary in some cases, and last touch favours the most recent message over
the one that first interested the customer. The record picks a rule that is
simple, reproducible and needs no parameter that could be tuned after the
outcome is visible. Whether a return that lands after a window has closed should
move a published result is the other judgement call, taken below. The estimator
for revenue per assigned customer, and the experiment's design values, belong to
the experiment-design ADR and are not decided here.

## Decision

Attribution and effect are two different measurements, defined separately, and
every report, export and contract field names which one it shows.

**Attribution** is commercial. It answers which campaign a sale is credited to,
and it is not a claim that the campaign caused it. A sale is attributed to at
most one campaign. A sale is eligible for the campaign of an experiment when it
is a recorded conversion (ADR-0019) of a treatment-arm assignment of that
experiment, the customer has a recorded exposure on that assignment before the
sale, and the sale falls inside the experiment's own `conversion_window_days`.
The attribution window is that same window: it opens at the exposure and closes
where the conversion window closes, `assigned_at` plus `conversion_window_days`.
It introduces no second duration, so it is fixed before the first assignment by
RN-25 and cannot be changed after the outcome is visible. A control assignment
is never exposed (ADR-0027) and is never attributed; an experiment with no
campaign (`experiment.campaign_id` is null) has conversions and an effect but no
attribution. When a sale is eligible for more than one campaign, the one whose
eligible exposure is latest before the sale holds it (last touch); a tie goes to
the lower `assignment_id`, so the result does not depend on evaluation order.
The holder, once recorded, is never reassigned, and no second holder is added.

**Effect** is causal and experimental. It is the difference between a treatment
arm and the control arm of one experiment, over every assigned customer whether
exposed or not, with its 95% interval and its test (ADR-0019). Only that
difference, and a quantity derived from it with its interval, may be called
incremental, uplift or caused. Attributed revenue, converted revenue and the
revenue of a treatment arm may not. An experiment with no control has an
attribution and counts but no effect (ADR-0028). Effect is per experiment and is
not summed across experiments; a sale that converts in two experiments counts in
each experiment's own comparison and is attributed once.

**A reversed sale.** Attribution and conversion are read net (ADR-0062), and no
recorded row is edited (ADR-0050). The attribution row for a sale stays and its
holder never changes, even when the sale is later cancelled; the holder is not
reopened for another campaign. Attributed revenue is the sale's net amount, read
through the net views at read time, so a partial return lowers it and a sale
whose net amount is zero contributes zero revenue and stops counting as an
attributed sale. A conversion row stays, but it counts as a conversion only
while its sale's net amount is above zero. A customer whose only qualifying sale
is fully reversed is not converted, or pending while the window is open; a
customer with another qualifying sale with a positive net amount in the window
remains converted. Revenue per assigned customer in both arms uses net amounts.
A return recorded after the window closes (ADR-0062 allows up to 30 days by
default) therefore changes later reads of conversion and revenue, never the
rows. Every result and report carries the time it was computed and is marked
"as of" it, so two reads at different times are not mistaken for a
contradiction.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Let a sale count for every campaign it converts for | Campaign totals then add up to more than the period sold, which is the defect the story names. A marketer cannot compare campaigns by a figure that double-counts |
| First touch: the earliest exposure holds the sale | It credits a message the customer may have forgotten and ignores the later one nearest the purchase. Both are arbitrary; last touch is the common default and is the one a floor user can explain. Either needs the same tie-break |
| Split a sale's credit across the campaigns that qualify | Fractions sum to the sale, but no single campaign holds it, which contradicts "at most one", needs an invented weighting that can be tuned afterwards, and complicates the schema refusal F25-12 asks for |
| Attribute to the biggest discount or the oldest campaign | It ties credit to a property of the campaign rather than to the customer's contact with it, and rewards campaigns for being generous or early |
| A separate attribution window, for example 30 days after exposure | A second duration to fix and to defend. It could extend past the conversion window and credit sales the experiment never counted as conversions. Reusing `conversion_window_days` keeps one window, already fixed by RN-25 |
| Attribute every sale of the treated group and call it the campaign's revenue | It reports the treated group's whole spend as caused. Without the contemporaneous control it cannot separate seasonality or customers who would have bought anyway (ADR-0019) |
| Call attributed revenue "incremental" when a control exists | The word then means two things. Incremental is reserved for the difference against control, with its interval, so a report that uses it is always an effect |
| Delete or reassign an attribution when its sale is reversed | Conversions and the measurement chain are append-only (ADR-0050), and reassigning a sale to another campaign after the fact changes both campaigns' totals after the outcome is visible |
| Freeze conversion and revenue when the window closes, ignoring later returns | It reports revenue the business has since refunded. ADR-0062 says every reader counts net, and a frozen figure would be the one reader that does not |

## Consequences

**What this makes easy.** One sale has one holder, so campaign totals can be
summed and checked against net sales. A report's claim is unambiguous: it either
says attribution or says effect. The rule uses only recorded events (assignment,
exposure, conversion, sale) and the existing window, so repeating the evaluation
attributes the same sales (F25-11, F25-12) and nothing new has to be fixed before
assignment. Reversals need no new rows or privileges.

**What this makes hard.** Attribution needs a recorded exposure. A treatment
customer who bought but has no recorded exposure is converted and counts in the
effect, but is attributed to nothing, so attributed revenue understates the
treated group's sales by design and will not equal arm revenue. Last touch is
arbitrary: a campaign that contacts customers earlier than another loses sales
to it even when it prompted the purchase. Because the holder is never reopened,
a sale held by a campaign whose conversion later reads as zero does not pass to
the runner-up. Results are a function of net sales at read time, so a published
number can change after a late return; the "as of" stamp is the cost of that, and
anyone comparing two reports has to compare the stamps. A late exposure could otherwise
displace a holder already written, so F25-12 must fix the holder only when the
window has closed, over the exposures that exist then, or order the evaluation so
the result is the same whenever it runs. That is an implementation constraint
stated here so it is not discovered later.

**What must now be true elsewhere.**
- F25-06 (#837) specifies attribution and result as separate resources or
  clearly separate fields, named so that none of the attribution fields uses
  "incremental", "uplift" or "caused".
- F25-11 (#842) records conversions as before and reads outcomes net, following
  the reversed-sale paragraph. It rewrites no conversion row.
- F25-12 (#843) adds the attribution table to `sql/01_schema.sql` with its 4NF
  justification and 30 seed rows. The decision the schema must carry is that the
  sale is unique in it (one holder per sale), that the attributed assignment is
  a treatment assignment of an experiment with a campaign, and that it is
  append-only for the application role (ADR-0050). The record fixes the rule, not
  the column list.
- F25-13 (#844) computes the effect from assignments, conversions and net
  purchases only, and refuses an effect for an experiment with no control. It
  uses the estimator the experiment-design ADR fixes.
- Report stories, including #849, #879/#880 and the demonstration in #896,
  label each figure as attribution or effect.
- `docs/business-rules.md` gains a rule for the attribution holder and the
  net-conversion reading, and RN-27's conversion paragraph is updated to refer to
  it when the first implementation lands.
- Nothing here changes the web system's boundary: the web gains no attribution.
  Placement of the attribution tables follows F19-02 and #838.

## Compliance

- A schema check proves one holder per sale: a second insert for the same sale
  fails on the unique constraint, as does an insert for a control assignment.
- A CI query over the seed proves that, for any period, the sum of attributed
  net revenue over all campaigns does not exceed the period's net sales
  (`net_sale`, ADR-0062).
- A test builds a customer in two experiments with one qualifying sale and
  asserts one holder, the latest exposure, and the `assignment_id` tie-break.
- A test cancels an attributed sale and asserts that the attribution and
  conversion rows are unchanged, the attributed revenue is zero, the customer is
  not converted, and no row of `experiment_conversion` was updated or deleted.
- A contract test scans the experimentation OpenAPI and XSD and fails if
  "incremental" or "uplift" appears on any attribution field, or if a result
  field that carries effect has no interval.
- A report test asserts every attributed-revenue figure carries the label
  "attribution" and every effect figure the label "effect", plus the "as of"
  time.
