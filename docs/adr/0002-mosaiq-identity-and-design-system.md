# ADR-0002 — MOSAIQ is the product identity, and the interface follows a custom token-based design system

**Status:** Accepted
**Owner:** Marcelo
**Issue:** #87 (F0-02b); resolves Q-3 and Q-4 of `docs/scope.md` §8, originally
bundled in the now-closed #46 (F0-02) and split into F0-02a/F0-02b by #88.
**Supersedes:** —
**Superseded by:** —

---

## Context

`docs/scope.md` §8 carried two open questions the team had not answered:
Q-3 (company name / brand identity) and Q-4 (design system for the
interface). Both needed a decision before F3-01 (application skeleton) and
F3-08 (apply the design system across the interface) could be built without
guessing, and F0-02b's acceptance criteria require the decision recorded as
an ADR with Q-4 removed from the open-questions table.

`docs/backlog.md` already notes Q-3 as informally resolved — "the company
name is MOSAIQ" — but `docs/scope.md` §8 was never updated to match, and no
ADR existed for it. The interface constraints are fixed regardless of naming:
one server-rendered Flask + Jinja2 application (C-2, C-3), a dense analytical
tool read by analysts, managers, and auditors for hours at a time over tables
of 200-800 rows — not a consumer app.

The design system itself was produced with Claude Design against a detailed
brief (three-tier CSS custom-property architecture, no client framework,
neutral chrome with color reserved for data, WCAG-checked data palettes,
26 components, two deliverable sheets). It is committed under
`docs/design-system/` (see that folder's `README.md` for the file index).
Two pieces of the generated brief exceed this delivery's current scope and
are called out below rather than silently dropped or silently built.

## Decision

The product is named **MOSAIQ**, and its interface follows the token-based
design system in `docs/design-system/`: three tiers of CSS custom properties
(primitives → semantic → component), a neutral chrome with four reserved
color families (semantic state, service health, and two ordinal/categorical
data scales), IBM Plex Sans/Mono typography, and a components layer of plain
semantic HTML with `mq`-prefixed BEM classes meant to be copied directly into
Jinja2 templates — no React, Tailwind, or build step.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| An off-the-shelf CSS framework (Bootstrap, Bulma) | Would have covered generic chrome but not the domain-specific pieces the brief required — bi-temporal filter bars, RFM score cells, segment badges legible at 10 simultaneous values, colorblind-safe categorical/sequential/diverging data palettes. Adapting a framework's component classes to those needs would fight the framework more than a from-scratch token system costs to build. |
| Leaving Q-3 unresolved in this ADR and handling it separately | The design system was already built entirely around the name (wordmark, `--mq-*` token prefix, BEM class prefix); documenting the design system without also closing Q-3 would leave the ADR referencing a "placeholder" name that is in fact already load-bearing throughout the committed CSS. |

## Consequences

**What this makes easy.** F3-08 has an exact, already-written specification
to implement against: `guidelines/component-sheet.html` is copy-paste-ready
Jinja2 source material, and `guidelines/foundations-sheet.html` is the
single reference for every token value. No visual redesign risk late in the
delivery.

**What this makes hard.** The design brief that produced this system
proposed two pieces that conflict with this repository's constraints, and
neither is implemented:

- **Service Health Board** (a live microservice-monitoring screen) conflicts
  with C-7 (no microservices). It is documented in the original Claude
  Design export but not carried into `docs/design-system/`, and must not be
  built unless C-7 itself changes via its own ADR.
- **The Highcharts theme** (`docs/design-system/charts/mosaiq-highcharts-theme.js`)
  renders client-side from chart data serialized into the page. This is
  *designed but not implemented*: before F3-08 wires any chart into a Jinja2
  template, the team must confirm that data embedded in server-rendered HTML
  for a chart to read does not count as the "JSON exchanged between internal
  components" C-2 prohibits — C-2 was written for API-style exchange between
  deployable units, not for values a single Flask process renders into its
  own page, but that reading is this ADR's judgment call, not a settled fact.

No font files or icon set were supplied with the source material; both are
open items listed in `docs/design-system/README.md`, not blockers.

**What must now be true elsewhere.** `docs/scope.md` §8 no longer lists Q-3
or Q-4 as open. Any future screen (F3-08 onward) is reviewed against
`docs/design-system/guidelines/foundations-sheet.html` and
`component-sheet.html`, not designed ad hoc.

## Compliance

A reviewer checks a new template against `docs/design-system/components.css`:
every class used must exist there with the `mq-` prefix, and no inline
`style="color:…"` / `style="background:…"` should appear where a token class
would do. Until F3-01 exists there is nothing to lint automatically; once the
application skeleton lands, this is enforceable as a CI grep for
`style="` in `*.html` template sources plus a check that referenced `mq-`
classes resolve in `components.css`.
