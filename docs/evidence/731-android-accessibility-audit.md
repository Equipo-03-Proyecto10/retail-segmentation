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

## Guard

`AccessibilityGuardTest` renders 32 screen states (sign-in, check-in, picker, scan, sale, registration, insights,
outbox, shell bars; six of them at 200% font), then walks the merged semantics tree. It fails if a node with a
click action has no text, description or merged child text, or if a `Role.Button` is under 44dp. A new screen state
needs one `audit { ... }` test.

## Not verified

TalkBack and Accessibility Scanner were NOT run: there was no device or emulator in this session. The checks above
come from reading the code, the Robolectric screenshots and the Compose semantics tree, none of which replace a
phone. A reviewer should, on a phone with TalkBack on:
- Swipe through each of the four tabs and listen for order, duplicated names ("Scan Scan"), and the roles spoken.
- Make a check-in, save a sale and trigger a sign-in error, and confirm the result is announced without moving focus.
- Set the font to the largest size and the display to the largest size, and open the sale screen, the store picker and the outbox.
- Run Accessibility Scanner on each screen for touch-target and contrast reports.
- Check the camera preview announces its description and that "Look up" is reachable without the camera.
