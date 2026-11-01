# ADR-0067 — A recommendation is judged at a reference store and channel, offers a declared alternative when unavailable, and its explanation is recorded

**Status:** Proposed
**Owner:** Maximiliano
**Issue:** #825
**Supersedes:** — *(it restates RN-40's eligibility test; it does not edit accepted ADRs. The register entry E-17 and `docs/services/recommendations.md` are updated by the stories listed under "What must now be true elsewhere")*
**Superseded by:** —

---

## Context

RN-40 (`docs/business-rules.md`) recommends a product only when it is active, has a
positive quantity at the customer's **usual store** (the consumption profile's dominant
store, RN-35), and the customer has not bought it in the window. It says that a product
held only by another store never appears. The web implements it in `recommend`
(`web/services/recommendations.py`), and recommendations-service (port 5011, F14-22)
is a port of it, pinned by golden fixtures for every seeded customer (ADR-0045, E-17).

The third delivery asks for more (`docs/scope-delivery-3.md`, RT-07 and the third
objective): a recommendation is useful only if the product is available in the relevant
channel and store, or a valid alternative is offered, and the service can explain it.
Today there is no reference store or channel other than the usual store, no discontinued
status, no alternative, and the explanation lives only in a response and a 120 s cache
(`docs/services/recommendations.md`: "Owns no table").

What is genuinely uncertain, and judged here:

- A seller stands in a store, and that store is not necessarily the customer's usual one.
  Recommending against the usual store would offer what the seller's shelf does not hold.
- ADR-0061 (Accepted) already defines availability as derived, per store and channel: the
  commercial status allows the sale, the store enables the channel, and on-hand is above
  zero. This record reuses that definition instead of inventing a second one (ADR-0059:
  "availability is RN-40's own eligibility test, not a second rule").
- The inputs to that definition do not exist yet. Commercial status and product relations
  are F21-09/F21-10 (#789, #790); store channels, hours and status are F21-11/F21-12
  (#785, #786); availability by store and channel is F21-07 (#787); consent and
  restrictions are F21-15 (#793), still undecided. This record fixes the semantics and
  names the fields it needs; it does not fix their column names or enumerations.
- Persisting the explanation makes recommendations-service the first owner of a table,
  which it was deliberately not (E-08 classes it as a read-rule service).
- The retention period is a judgement call. No retention rule exists yet in the repo
  (F21-15 decides one for personal data); the figure below is a proposal.

## Decision

recommendations-service judges every recommendation at a **reference store** and an
optional **reference channel**, offers a declared alternative for a product that is
unavailable there, and records one explanation per recommendation it returns. The caller
may pass `store_id` and `channel_id`; the service never guesses either.

**Reference store and channel.**

| Caller gives | Reference store | Reference channel |
|---|---|---|
| Nothing | The customer's usual store (RN-35), exactly as RN-40 today | None: availability is judged at the store, over any channel the store enables |
| `store_id` | That store. A floor role is accepted only at a store assigned to it (ADR-0060); another store is 403 | None, unless given |
| `channel_id` | The usual store, or the given one | That channel. An unknown id is 422 |
| Nothing, and the customer has no usual store | None: `no_usual_store`, as today. The service does not substitute a store, a default or "all stores" | — |

A given `store_id` is the seller's declared position and takes precedence over the usual
store. The usual store remains the default because it is the only reference RN-40's
accepted behaviour and its golden fixtures know. The channel is never defaulted from the
customer's dominant channel: that is a habit, not a statement of where the sale happens.
Both ids are query fields, never path segments (AGENTS.md: foreign ids are query or body
fields).

**Available** means, at the reference store (and channel, when given), all of:

1. the product's **commercial status** allows the sale (F21-09 names the values; `active`
   alone is no longer enough, and a discontinued product does not qualify);
2. the **store sells**: its status permits sales, and, when a channel is given, the store
   enables that channel (F21-11);
3. **on-hand is above zero** (`api_stock.stock_level_v1`, as RN-40 reads it).

Hours are not part of availability: a closed shop reopens, and a recommendation is not a
sale. The sale itself still refuses what is unavailable (RN-14).

**Exclusion reasons.** A product that fails is excluded for exactly one reason, taken in
this order so the reason is deterministic:

| Code | Excluded because |
|---|---|
| `already_bought` | The customer bought it in the window (RN-40) |
| `inactive` | The product's `active` flag is false |
| `not_sellable` | Its commercial status does not allow the sale, such as discontinued |
| `store_not_selling` | The reference store's status does not permit sales |
| `channel_not_enabled` | A reference channel is given and the store does not enable it |
| `out_of_stock` | On-hand at the reference store is zero |
| `personalization_declined` | The customer's consent or restrictions forbid it (see Consent) |

`no_signal` (matches no segment, preferred category or purchase signal) stays what RN-40
says it is, a failure of relevance and not an availability reason. Exclusions are
counted per reason in the response and the record. Products are not listed, except where
an alternative replaces one.

**Valid alternative.** When a product that matches a signal fails availability only for
`not_sellable`, `channel_not_enabled` or `out_of_stock`, the service may offer an
alternative in its place, at most one per excluded product, and never for `inactive`,
`already_bought` or a consent exclusion. An alternative qualifies only when it:

- is **related by a declared product relation of kind substitute** (F21-09's relations;
  complements and accessories do not qualify; a same-category guess is rejected because
  nothing declares it);
- is itself available at the reference store and channel by the definition above, is not
  already bought in the window, and is not already in the result;
- is not itself excluded by the customer's consent or restrictions.

Offered alternatives are marked in the response: `kind: "alternative"`, with
`alternative_for` carrying the id of the unavailable product and a reason code such as
`relation_substitute`. They never take a primary slot that an available matching product
could fill. **Another store may be offered only as a marked alternative**, never as a
recommendation: `kind: "elsewhere"`, carrying that store's id and its quantity, when no
substitute is available at the reference store; it is the nearest store (by the stores
contract's coordinates) that is selling the product on the given channel, at most one per
product. This does not change RN-40's rule that a product held only elsewhere is not
*recommended*: it is *reported* as elsewhere and cannot be mistaken for in-store stock.

**Recorded explanation.** The service records, per recommendation it returns (primary,
alternative or elsewhere), in a `recommendations` schema it owns (ADR-0046): a decision id;
the customer id; the reference store id and channel id as given or derived (and which);
the product id and its category id; the kind; the `alternative_for` product id; the
position; the quantity on hand it used (E-06); the signals and their figures, one row per
reason in the fixed RN-40 order; the segmentation run and the rule version that produced
it; and the computation time. Per decision it also records the exclusion counts per reason
and the status. It stores **identifiers and figures only**: no name, email, phone, SKU,
price or free text, and nothing copied from customer or product beyond ids. Reasons are
rows, not a JSON blob, so the structure is normalized (4NF: the signals of one
recommendation are a multivalued fact independent of its other attributes). Records are
kept **90 days** and then deleted by the service's own job; a customer's anonymization
(F21-19) removes the customer id from the row and leaves the figures. The record is the
explanation; the response is only a view of it. The decision is exposed through a new
top-level resource owned by this service, `recommendation-decisions` (ADR-0035), read by
`ADMIN`, `ANALYST`, `AUDITOR` and the supervising roles; the exact roles are set in F24-03.

**Parity with the web (E-17).** The web does not change: it stays frozen (ADR-0045) and
keeps the usual store. When the caller gives neither store nor channel and every product
is `active` and sellable, the service must return what the web returns, and the golden
fixtures keep pinning that **default path**. The new behaviours (a given store or channel,
`not_sellable`, `store_not_selling`, alternatives, the record, consent) are not in the web
and are **redefined out of the parity claim**: E-17 is rewritten to say parity holds on the
default path only, and the fixtures record each deliberate difference with its reason, as
ADR-0045 already allows. The web does not adopt the rule, because it would need the
stock-channel, commercial-status and consent contracts it cannot read (it reads
PostgreSQL directly and cannot reach `stock.*` or `users.*`, ADR-0046), and the web is frozen.
The visible difference is one the record accepts: a discontinued-but-active product still
shows on the web and not from the service.

**Consent.** A customer who has objected to personalization (a recorded restriction,
F21-15) is not recommended to. The service answers `status: "personalization_declined"`,
with empty items, and reads no segment, purchase history or interest for that customer
(nothing is substituted, as for `no_open_assignment`). It records a decision with the
status and no items and no signals. A customer who restricts only some signals (for
example inferred interests) has those signals dropped and the others kept. The mapping of
the restriction values to signals, and the point from which an objection applies
(effective at once, to cached results too, because the cache is invalidated by the
preferences event), are F21-15's to fix; this record only fixes the outcome.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Keep the usual store as the only reference | A seller in another store would be offered what that shelf does not hold, which is the defect the third objective names |
| Default the channel from the customer's dominant channel | It filters on a habit, not on where the sale happens, and would silently change the default path the golden fixtures pin |
| Fall back to a default store or "all stores" when the customer has none | RN-40's principle is that nothing is guessed; a guessed store makes the stock figure meaningless |
| Offer any other-store stock as a normal recommendation | It contradicts RN-40 and the client cannot tell stock here from stock elsewhere |
| Same-category products as alternatives | Nothing declares them as substitutes; the explanation would be a guess. A relation declared in F21-09 is evidence |
| Store only the response, as the cache does now | It lasts 120 s, and nothing could answer "why was this shown last month" (F26-04 and F27-06 need it) |
| Store the explanation in MongoDB or in engagement-service | Engagement owns what happened in store (impressions, clicks); the decision is recommendations' own rule. Engagement may reference the decision id, not hold it |
| Store reasons as one JSON column | It hides a multivalued fact and cannot be constrained or aggregated for F26-04 |
| Make the web adopt the new rule | The web cannot read the stock, store and consent contracts, is frozen, and would break its own accepted RN-40 tests |
| Re-pin all parity to the new behaviour | The new behaviours do not exist in the web, so there is nothing to pin to |

## Consequences

**What this makes easy.** "Available" and "explained" have one meaning, shared by the
contract, the Android screen and the inspection module. A stock change visibly changes a
result (F24-08). A seller gets what the shelf in front of them holds.

**What this makes hard.**
- recommendations-service gains a table, a schema, a write path, a retention job and a
  new role-to-resource policy. ADR-0059 and E-08 count it as a read-rule service; one with
  a schema of its own is no longer "no table", and the grant matrix and
  `docs/services/recommendations.md` change. The one rule is unchanged (RN-40's
  eligibility and explanation), but a reviewer may disagree that it still owns no
  invariant of its own.
- The service reads more (commercial status and relations, store channels and status,
  the consent restriction), so E-07's "seven published contracts" grows. The number is a
  consequence of the rule, not a reason to split it.
- Two behaviours exist: the web's and the service's. The cost is the one E-17 already
  accepts, widened by a stated difference.
- Recording each decision costs writes on a read path. Idempotent recompute within the
  cache window must not write twice.

**What must now be true elsewhere.**
- F24-03 (#826), blocked by this record, publishes the contract: `store_id`, `channel_id`,
  `kind`, `alternative_for`, the exclusion counts and `personalization_declined`, the
  `recommendation-decisions` resource in `contracts/registry.yaml`, JSON and XML, and the
  database section of `sql/01_schema.sql` (idempotent, with its seed, or an exemption
  named in `sql/seed-exempt.txt`).
- F24-04 (#827) filters by availability; F24-05 (#828) records the explanation; F24-06
  (#829) applies consent; F24-07 (#830) offers alternatives; F24-08 (#831) proves a stock
  change updates the result.
- F27-06 (#860) and F28-08 (#877) display and inspect the kind, the reasons and the
  decision id.
- F21-09 and F21-11 must publish a sellable test, substitute relations, store status and
  channel enablement in their `api_*` views; F21-15 must define the objection and the
  restriction values and agree with the 90-day retention or change it.
- E-17, E-07, E-08 and `docs/services/recommendations.md` are updated in F24-03; RN-40 in
  `docs/business-rules.md` is restated there to the new eligibility, with the default
  path unchanged.
- The cache key `recommendations:cache:{customer_id}:{run_id}:{limit}` must include store
  and channel, or a seller's result would be served to another store.

## Compliance

- Golden fixtures for every seeded customer still pass on the default path (no store, no
  channel); each deliberate difference is a recorded fixture with its reason.
- A test per exclusion code: a product that fails only that one is excluded with that
  code, and the order of reasons is checked.
- A test that a given store beats the usual store, that an unassigned store is 403, and
  that a customer with no usual store and no store given gets `no_usual_store`.
- A test that an alternative is offered only through a declared substitute relation, is
  itself available, is marked, and is never offered for `inactive` or `already_bought`; and
  that an `elsewhere` entry is never unmarked.
- A schema check that the decision tables hold only ids, figures and codes (no text
  columns for names or contact data), and a retention test that rows older than 90 days
  are deleted and anonymization clears the customer id.
- A test that a customer with a recorded objection receives `personalization_declined`
  and that no segment, history or interest read ran for them.
- `contracts/db/check_grants.sh` both ways for the new schema's grants; `contracts/check.sh`
  for the contract.
