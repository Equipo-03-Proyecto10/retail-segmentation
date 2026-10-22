# #422 (F16-05): screenshots of every desktop screen, and one XSD failure

Captured from the real Electron window (Playwright driving the packaged `dist/`), against the same
in-process fake gateways the window tests use. The fakes answer with the recorded examples in
`contracts/<service>/examples/`, validated against each service's XSD by the client. No live backend,
no real data, no credentials: the people and addresses are the examples' invented ones.

Reproduce from `clients/desktop/`:

```
MOSAIQ_EVIDENCE_DIR=/some/dir npm run evidence
```

`tests/support/evidence.ts` takes a picture only when `MOSAIQ_EVIDENCE_DIR` is set, so `npm run
test:window` and CI take none. The issues for these stories name no RF or RN ids beyond RN-11 (#410),
so captions cite the story, the issue and the contract's own codes.

The desktop customers screen of F16-03 (#414) is already documented, against a live stack and the web,
in [`414-desktop-customers.md`](414-desktop-customers.md). One more shot of it is added here.

## F16-01 (#406): sign-in, role-gated services, error envelope, XSD failure

| Screenshot | What it shows |
|---|---|
| `406-desktop-signin-01-form.png` | The sign-in form of the locked-down window, before any session. |
| `406-desktop-home-02-signed-in.png` | A `MARKETING` session: "Signed in as MARKETING" and only the screens that role may use. |
| `406-desktop-services-03-forbidden-role.png` | The services screen, and a forced call refused with `forbidden_role`, shown with its message and the server's correlation id instead of being hidden. |
| `406-desktop-signin-04-error-envelope.png` | `invalid_body` on sign-in: code, message, correlation id, and the field detail `password: required` next to the form. |
| `406-desktop-contract-defect-05-xsd-failure.png` | XSD validation failure. The campaigns service answered 200 with a well-formed `CampaignList` whose `total` is the text "two" and which carries an unknown `<surprise/>` element. The client validated it against the campaigns XSD, refused it, and shows `contract_defect`, "answered outside its contract. Nothing of the response was used", and a correlation id. Nothing of the body, including `surprise`, reaches the screen. |

## F16-02 (#410): products and the category tree (ADMIN)

| Screenshot | What it shows |
|---|---|
| `410-desktop-products-01-list-with-category-names.png` | The product list; category names are composed from categories-service, not read from the product. |
| `410-desktop-products-02-product-detail.png` | One product opened: SKU and category name. Products have no delete control (RN-11). |
| `410-desktop-products-03-sku-taken-refusal.png` | Creating a product with an existing SKU: `sku_taken` shown as returned, with its correlation id. |
| `410-desktop-categories-04-tree-and-descendants.png` | The category tree and the descendants of a root category. |

## F16-03 (#414): customers

| Screenshot | What it shows |
|---|---|
| `414-desktop-preferences-03-channels-saved.png` | An ADMIN saves the whole preferred-channel set: the notice "Preferred channels saved." and the new set, after the PUT. |

## F16-04 (#417): stock, sales, engagement, check-ins

| Screenshot | What it shows |
|---|---|
| `417-desktop-stock-01-below-threshold.png` | Stock below a threshold, with product and store names composed from products-service and stores-service. |
| `417-desktop-sales-02-sale-detail.png` | A sale read back in XML: lines, total, channel and store. |
| `417-desktop-engagement-03-event-voided.png` | An ADMIN voids an engagement event: it stays listed as voided, with its history, and the summary count drops from 57 to 56. |
| `417-desktop-engagement-04-already-voided.png` | A void another user already made: `already_voided`, shown as returned. |
| `417-desktop-check-ins-05-review-appended.png` | A check-in with its reason code and an appended review below the earlier ones; no edit or delete control. |

## F16-06 (#484): users and the administrator transfer

| Screenshot | What it shows |
|---|---|
| `484-desktop-users-01-list-and-detail.png` | The users list filtered to an inactive user and its detail; accounts are deactivated, never deleted. |
| `484-desktop-transfer-02-warning-dialog.png` | The transfer confirmation dialog, warning that both accounts' sessions end. The refusal behind it is the earlier "no successor chosen" check, refused before sending. |
| `484-desktop-transfer-03-successor-inactive.png` | The service refuses an inactive successor: `successor_inactive`, shown as returned. The one-administrator rule stays with the service. |

## F16-07 (#485): store locations and geofences

| Screenshot | What it shows |
|---|---|
| `485-desktop-stores-01-directory-and-location.png` | The store directory, one store with a location and geofence radius, one marked "no location". Plain numbers, no map. |
| `485-desktop-stores-02-out-of-range-refused.png` | Latitude, longitude and radius outside their bounds are refused before sending, each naming its field (`out_of_range`). |

## F16-08 (#486): campaigns

| Screenshot | What it shows |
|---|---|
| `486-desktop-campaigns-01-list.png` | The campaign list with label names from segments-service, statuses and the page count. |
| `486-desktop-campaigns-02-detail.png` | One DRAFT campaign: label, dates and status. |
| `486-desktop-campaigns-03-ends-before-start.png` | `ends_before_start` shown as returned, with `ends_on: before_start` next to the date field. |
| `486-desktop-campaigns-04-moved-to-active.png` | After a DRAFT to ACTIVE move: the new status and only the moves ACTIVE allows. |
| `486-desktop-campaigns-05-illegal-transition.png` | `illegal_transition` refused by the service and shown as returned. |

## F16-09 (#487): audit trail

| Screenshot | What it shows |
|---|---|
| `487-desktop-audit-01-list.png` | The audit trail as an AUDITOR sees it: entries, filters, no write control. |
| `487-desktop-audit-02-entry-detail.png` | One entry's before and after as formatted text. |
| `487-desktop-audit-03-invalid-query.png` | An invalid filter: `invalid_query`, shown as returned. |

The window is 1100 px wide, so some screens are cropped to the viewport; the cropped part is not
needed by the caption.
