# Android accessibility audit (#731, UX-13)

Scope: every screen and state of the Android client, read from the composables and from the Robolectric
screenshots (light, dark and 200% font). Severity: H = a person using TalkBack or large text cannot do the task or
is misled; M = the task works but is confusing or noisy; L = polish.

Already correct before this pass (kept, now guarded by `AccessibilityGuardTest`): buttons are 48dp; icon-only
buttons carry descriptions; the camera viewport has one; decorative icons are `contentDescription = null`;
badges, segments and errors carry text; section titles use `heading()`; clickable rows are `Role.Button`;
checkbox and radio rows use `toggleable`/`selectable` with a role; loading rows are a polite live region.

| Screen | Sev | Finding | Fix |
|---|---|---|---|
| Sale: customer card, 200% font | H | The email wrapped mid-word ("ana@exam / ple.com") beside the Change button. | Past a 1.3 font scale the button drops below the text (`CustomerSection`). The name and contact merge into one node. |
| Shell: bottom bar, 200% font | H | Labels broke mid-word ("Check / -in", "Outbo / x"). | Past 1.3 only the selected tab draws its label. The label stays in the semantics tree once (asserted by `shellBarsLargeFont`). |
| Shell: top bar, 200% font | H | The store chip was cut to "Ce..." by the wordmark. | The wordmark is dropped past 1.3 so the store name reads in full. |
| Shell: Outbox tab | M | The badge read as a bare "3". | The badge reads "3 writes waiting to send" (plural resource `a11y_outbox_waiting`). |
| Shell: top bar | L | The wordmark was not a heading. | Marked as a heading. |
| Pager (picker, customers) | M | At 200% the three controls squeezed one row. The page change was silent. | Past 1.3 the label sits above Previous and Next. "Page 1 of 2" is a polite live region. |
| Quantity stepper | M | The count read as a bare number. | Reads "Quantity 2" and announces changes. |
| Text fields | M | An error showed only as a red border plus text, with no error state in semantics. | `error(errorText)` is set, so TalkBack says the message when the field is focused. |
| Plain list rows (Outbox, store lists without click) | M | Headline, time and badge were three stops. | The row merges into one node. |
| Check-in | M | The status badge, result and note changed silently after a tap. | Polite live regions on all three. |
| Sign-in | M | The notice (for example "signed out") was not announced. | Polite live region. |
| Sale: Record sale, status line, interests saved, registration channel error | M | "Sale saved", the sync message, "Interests saved" and the channel error were silent. | Polite live regions. |
| Sale: history row | L | Text and badge were two stops. | Merged into one node. |
| Sale: cart line | L | Name and unit price were two stops. | Merged. The buttons beside them stay separate. |
| Sale: "Cart" title, Scan store caption, product name | L | Titles were not headings. | Marked as headings. |
| Insights | L | The segment dot and badge were two nodes. The click-refusal note was silent. | The segment merges into one node. The note is a live region. |
| All Role.Button controls (sign-in, check-in, picker, scan, sale, insights, outbox, shell) | - | Checked for a label and a size of at least 44dp. | None needed. Now an automated guard. |
| Stepper buttons | L | "Decrease quantity" does not name the product. | Not fixed: `SaleContentTest` and `ComponentsTest` match the exact description. Each line reads its product name just before the buttons. |
| Product row "Add X" icon | L | The icon repeats the headline in the row's merged name. | Not fixed: redundant but harmless, and `SaleContentTest` relies on it. |
| Store picker: search button, 200% | L | The Search button sits a few dp above the field. | Not fixed: cosmetic, nothing is clipped. |
| Status badges ("Status: Waiting to send") | L | A prefix was suggested. | Not fixed: the badge text already names the state, and the row reads it with the operation and time. |
| Insights segment dot color | - | Color is the dot only. | None needed: the badge text always carries the segment. |

Counts: 3 H, 8 M, 6 L fixed or confirmed; 4 L not fixed (reasons above).

## Second pass (closing #731)

The first guard sized only `Role.Button` nodes, never checked that a control had a role, and ran six states at
200% font. It now runs every state at 1.0x and at 2.0x with Robolectric's native text measurement. It reported the
first two rows below. The other rows come from reading the screens again against each acceptance criterion.

| Screen | Finding | Fix |
|---|---|---|
| Store picker, 200% font | The City field sat in an intrinsic-height row beside Search, which cut its typed text. | Past a 1.3 font scale, Search goes below the field at full width. |
| Shell: account menu | "Sign out" in the menu was clickable with no role, so TalkBack did not say "button". | The menu item carries `Role.Button`. |
| Sale: cart line stepper | Every line's buttons read "Decrease quantity" and "Increase quantity" without the product (an L left open above). | `MqQuantityStepper(itemName = …)`: "Increase quantity of Trail socks". |
| Sale: registration form | The channel error was red text alone. | `MqInlineError`: an alert icon beside the text, merged and announced politely. |
| Insights | The click-refusal note was red text alone. | `MqInlineError`, as above. |
| Outbox | Before Room's first answer the screen said "Everything is synced". That was false, and TalkBack read it too. | The outbox shows a loading state until the queue has been read once. |
| Every screen (guard) | Roles, clipped controls, cut text and most states at 200% were not checked. | The guard checks all of them, at both font sizes, for 33 states (122 after the third pass). |
| Every screen (code) | Three private copies of the 1.3 large-font threshold. | One `isLargeFont()` in the theme. |
| Insights error, 200% font | Drawn alone in a test, the panel's Details row ran off the bottom. | No fix needed: the card always sits in the sale screen's scrolling column, and the guard now hosts it there. |
| Text fields | A field has a click action and no role. | No fix needed: TalkBack announces it as an edit box from its class. |

Counts for this pass: 10 findings, 8 fixed and 2 needing no fix. The 200% "Search button a few dp above the field"
item above is gone with the stacked layout. "Product row Add X" and the badge prefix stay as written above.

## Third pass (#1122): the screens added with #1120

The guard of the second pass covered the screens on `develop` at the time. Integration PR #1120 added the screens
below. Each is drawn by its stateless composable at 1.0x and at 2.0x font scale, with the same five checks (label,
role, 44dp target, clipped control, cut text). Panels that live inside a scrolling parent in the app (a card, a
section) are hosted in a `verticalScroll` column here, as `insightsCard` hosts the insights card. The guard found no
problem on any of them, so no screen changed.

| Screen (story) | Composable | States drawn | What was checked |
|---|---|---|---|
| Identify (#856) | `IdentifyBody` | idle, camera on, camera error, camera denied, looking up, not a loyalty card, unknown card, revoked card, card lookup failed, search results with a pager, empty search, searching, search failed | Scan, grant-access, settings, retry and "Scan again" buttons and every customer row are labelled and at least 44dp. The search field, its button and the pager do not clip at 200%. Unknown and revoked cards show text and an action, not an icon alone. |
| Identify profile (#856, #860) | `ProfileBody`, `RecommendationsPart` | full, objected to personalization, loading, error, each of the three parts failed, unassigned segment, recommendations (primary, alternative, elsewhere; tappable and read-only), loading, no store, error | The segment, interests and restrictions parts keep their text at 200%. A recommendation row is one labelled node, a button only when the role records taps. "Alternative" and "Elsewhere" are badges with text. The error panels offer a retry button. |
| New customer (#863) | `NewCustomerBody` | empty, every consent answered, sending, refused, registered with the unknown-outcome advice | The three consent questions are radio rows with roles and labels, each 44dp or more at 200%. The submit button is full width, and while a consent is unanswered a line of text says so. |
| Customer record (#863) | `RecordBody` | full, loading, error, saved/saving/refused writes per part, history open (consent and preference changes), history loading, empty and failed | The consent choices, the channel and interest checkboxes and the three save buttons have roles and labels. The "who may change this" lines and history rows are not cut. The history toggle is reachable by scrolling. |
| Promotions (#861) | `PromotionsContent` | listed (one recorded), none, loading, error, arm unknown, note | Each "Shown to customer" button is labelled and at least 44dp. The recorded state is a text badge, not a colour. The discount and validity lines wrap at 200%. |
| Updates inbox (#869) | `InboxContent` | full with refused writes, notifications off, focused item, refreshing, empty, never polled, both sources failed | The refresh and "Open outbox" buttons are labelled. The "Ends soon" badge carries text. Headline, subject and time are not cut at 200%. |
| Indicators (#862) | `IndicatorsContent` | shown (every contract example), loading, store refused, every report refused, a definition open | The period chips have roles and 44dp targets, each figure is a labelled button that opens its definition, and the stock alert rows and the definition panel do not clip at 200%. |
| Catalog search (#858) | `CatalogSearchSection` | idle, searching, found (with a retired product and a "showing 3 of 25" note), found nothing, failed | The query field and search button are reachable. Each result row is one labelled button with a chevron. The retired product reads "no longer sold" in text. |
| Availability (#858) | `ScanAnswer` | sellable (add and read-only), out of stock, not stocked, channel not enabled, paused, discontinued, retired, stock read failed, barcode not found, invalid barcode | The stock, status and "not sellable" badges carry text. "Add to sale" and "Scan another" are labelled, full width and 44dp or more. The explanation of an unsellable product is not cut at 200%. |
| Cart, summary, receipt (#859) | `ReviewContent`, `SaleOutcome`, `SaleBar`, `HistorySection`, and the sale screen | summary, confirming, refused on confirm; receipt (recorded, replayed), refused naming the line; bar (ready, blocked, confirming, queued, recorded, refused); history (mixed pending, done and refused; empty; status message and error); the whole sale screen with a promotion line, a receipt and a kept cart | The summary is drawn through `ReviewContent`, not `ReviewSheet`, because a `ModalBottomSheet` is a separate window the semantics walk does not reach. Confirm and "Back to cart" are labelled and 44dp or more. Receipt lines, the discount and the total are merged text nodes. The refused row carries its reason as text next to the "Needs attention" badge. |

Counts for this pass: 9 screens (11 composables), 89 new states, 0 findings.

## Guard

`AccessibilityGuardTest` renders 122 screen states: the 33 of the second pass (sign-in, check-in, store picker, scan,
sale, registration, insights, outbox, the shell bars and the open account menu) and the 89 of the third pass (identify,
profile and recommendations, new customer and record, promotions, inbox, indicators, catalog search, availability, the
cart summary, receipt, sale bar and history). It draws each one at the
default font size and again at 200%, with native text measurement, and walks the semantics tree. It fails if any
of these hold:
- a node with a click action has no text, description or merged child text;
- a node with a click action has no role (text fields are exempt);
- a node with a click action is under 44dp;
- a control is clipped and no ancestor can scroll it back into view;
- a text overflows its height or is cut with an ellipsis.
A new screen state needs one `audit { ... }` test.

## Not verified

TalkBack and Accessibility Scanner were NOT run: there was no device or emulator in this session. The checks above
come from reading the code, the Robolectric screenshots and the Compose semantics tree, none of which replace a
phone. A reviewer should, on a phone with TalkBack on:
- Swipe through each of the four tabs and listen for order, duplicated names ("Scan Scan"), and the roles spoken.
- Make a check-in, save a sale and trigger a sign-in error, and confirm the result is announced without moving focus.
- Set the font to the largest size and the display to the largest size, and open the sale screen, the store picker and the outbox.
- Run Accessibility Scanner on each screen for touch-target and contrast reports.
- Check the camera preview announces its description and that "Look up" is reachable without the camera.
