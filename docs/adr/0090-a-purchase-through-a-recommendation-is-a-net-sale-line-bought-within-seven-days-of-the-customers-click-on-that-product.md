# ADR-0090 — A purchase through a recommendation is a net sale line bought within seven days of the customer's click on that product

**Status:** Accepted (2026-11-23)
**Owner:** Estefanía
**Issue:** #1197 (blocks #849, story F26-04)
**Supersedes:** —
**Superseded by:** —

---

## Context

analytics-kpis reports recommendation performance as impressions, clicks and purchases, each counted from its own origin and none derived from another (F26-04, #849). Impressions and clicks are interactions that engagement relays on `stream:engagement.v1.interaction_recorded` (ADR-0075). A purchase is a sale line, and a confirmed sale exists only in sales-service. Nothing yet says when a sale line counts as bought *through* a recommendation, so recommendations has no view to publish for it.

The judgement calls are three: which interaction earns the credit, how long it lasts, and whether anonymous interactions count. A shown recommendation may never have been seen, so an impression is weak evidence; a click is an act of interest. Seven days is the window conversion already uses (`conversion`, definition version 1).

## Decision

recommendations projects `RECO_IMPRESSION` and `RECO_CLICK` from the engagement stream into its own schema, one row per event, with a later `VOIDED` notice hiding the row, and publishes them as `api_recommendations.interaction_v1`. It publishes `api_recommendations.recommended_purchase_v1`: a net sale line (ADR-0062) whose customer made a non-voided `RECO_CLICK` on the same product no more than seven days before the sale, counted once per sale line, against the earliest such click. An impression never earns a purchase, a purchase is never inferred from a click count, and a sale without a customer is never a recommended purchase. Interactions without a customer are kept and counted as impressions and clicks only. The window and the rule are observational: they say a click preceded a purchase, never that the recommendation caused it.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Any recommended product bought within seven days (read from the stored decisions) | Counts products the customer may never have seen; it measures the rule's output, not behaviour. |
| A click or an impression within seven days | Mixes exposure with intent, so the purchase figure would move with the number of impressions, which the report must keep apart. |
| Keep only interactions that name a customer | Impressions and clicks at an anonymous counter would be undercounted. Only the purchase needs a customer. |
| A window other than seven days | It would disagree with the conversion window of the same service for no stated reason. |

## Consequences

**What this makes easy.** The three recommendation figures come from three separate origins, so none can be derived from another. The view is plain SQL over data the service owns and a published sales view.

**What this makes hard.** Figures are eventually consistent, because the interactions travel on a stream. A click that reaches recommendations after the sale still counts, since the rule reads event time, so a figure can grow after it was first read. A purchase has one credited click only; later clicks on the same product add nothing.

**What must now be true elsewhere.** RN-74 in `docs/business-rules.md`; the contracts `api_recommendations.interaction.v1.md` and `api_recommendations.recommended_purchase.v1.md`; analytics-kpis reads these views and not the stream (ADR-0059). Changing the window or the credited interaction is a new view version.

## Compliance

A test builds a click six days and eight days before a sale and expects a row for the first only; an impression, another customer or another product earns none; a void removes the click's credit; a fully reversed sale has no row. The grants check proves analytics-kpis reads the views and no table.
