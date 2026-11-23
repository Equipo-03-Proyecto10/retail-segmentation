# #733 (UX-17): every Android screen before and after the refresh, at 375 dp, light and dark

The screen tour of #445 was run again against the refreshed app (epic #716). It
uses the same real debug app, installed without `-g` and driven through its own
UI on an emulator. The app talks to the real services behind the local stack's
gateway, not to fakes. This time every screen is captured at **375 × 667 dp**,
in the light scheme and then in the dark one.

Each screen sits beside its #445 screenshot, the "before". The "before" was
taken on the delivery 2 app at 1080 × 1920 px and 420 dpi, which is
411 × 731 dp, in the light scheme only. The app had no dark scheme then.

## Where it was run

| Piece | Here |
|---|---|
| Date | 2026-11-22 (UTC-6) |
| Emulator | Android Emulator 37.3.10 on Linux (KVM), AVD on `system-images;android-33;google_apis;x86_64`. The display is overridden to 750 × 1334 px at 320 dpi, which is **375 × 667 dp**. |
| App | The debug build of `develop` at `78472561`, with base URL `http://10.0.2.2:8080/` |
| Stack | `compose.distributed.yaml` (project `mosaiq-local`): fifteen services behind the gateway on `127.0.0.1:8080`, with PostgreSQL, Redis and MongoDB. Every service is `develop`'s at `78472561`. The stack was started for #1143 from `8caea795`, and the services that differ from `develop` were rebuilt from it: sales and segments. rfm-engine differs only in its tests and `VERSION`, and compose rebuilt the same image. |
| Accounts | The seed's `user3@mosaiq-demo.com` (STORE_MANAGER) and `user14@mosaiq-demo.com` (ANALYST), with the published demonstration password (ADR-0008) |
| Scheme | `adb shell cmd uimode night no`, then `yes`. The app follows the system scheme. |

**To reproduce,** start that stack and install the app, then run this from the
repository root:

```sh
ADB=adb uv run --with pillow python clients/android/evidence/screen_tour.py docs/evidence all
```

[`screen_tour.py`](../../clients/android/evidence/screen_tour.py) is #445's tour,
rewritten for the refreshed app:

- it sets the 375 dp display and puts the device's own size back at the end;
- it finds each control by its text in a `uiautomator dump` and taps it with `adb shell input`;
- every vertical swipe stays between y 250 and 700 px, so it always lands on the
  scrolling content. At 375 dp the sale screen's sticky total starts at y 716,
  and #445's swipe from y 1500 started below the screen;
- each screen is shot in light, then dark, at full size (750 × 1334);
- a screen that was scrolled is scrolled into place again after each scheme
  switch, because the switch recreates the activity.

The #445 version is the one committed in `9bd6f0eb`.

**How these captures were taken.** They come from one run of the tour. It
stopped once, at step 20, on a bug in the tour itself: it looked for the
product search below the screen when it was above. Once that was fixed, steps
20 to 25 ran on from the same state. A later run of the fixed tour passed steps
1 to 22 unattended. It was then stopped at step 23 by a second `uiautomator
dump` that was started by hand while it ran.

**The database was not fresh.** It held #1151's local-only campaign 31,
promotions 31 and 32 and sales 301 to 303, and the sales and records of the
dry runs of this tour. So customer 3's interests and recommendations differ
from #445's, and the recorded sale is 307, not 303.

## The navigation, before and after

#445's app had one row of tabs that scrolled sideways: Store & check-in, Scan a
product, Customer & sale, Outbox, and Sign out. The refreshed app has a top bar
with the store and the account menu. It has a bottom navigation of the
role's own destinations, which for the store manager are Check-in, Scan, Sale,
Indicators and Outbox (ADR-0058, #855). The customer, their interests, segment
and recommendations, and the sale are now one Sale screen with a sticky total.

## F17-01 (#426): sign-in, the role gate, sign-out

### 01 The sign-in form

Sign in stays disabled until both fields hold something.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: sign in](445-android-session-01-sign-in.png) | ![After, light: sign in](733-android-session-01-sign-in-light.png) | ![After, dark: sign in](733-android-session-01-sign-in-dark.png) |

### 02 A wrong password

auth-service refuses it, and the error panel says so in plain words. The code
and correlation id are under Details. Before, the password was cleared. Now
what was typed is kept (#724). The switch to dark recreates the activity, and a
password is never kept across that, so the dark capture has an empty password
and no Retry.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: wrong password](445-android-session-02-wrong-password.png) | ![After, light: wrong password](733-android-session-02-wrong-password-light.png) | ![After, dark: wrong password](733-android-session-02-wrong-password-dark.png) |

### 03 An analyst is signed out at once

The message now names the floor roles that may use the app (#855). As in 02,
the dark capture has an empty password, because the switch recreates the
activity.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: analyst refused](445-android-session-03-analyst-refused.png) | ![After, light: analyst refused](733-android-session-03-analyst-refused-light.png) | ![After, dark: analyst refused](733-android-session-03-analyst-refused-dark.png) |

### 04 Signed in: asked for the store

Before, one screen held the name, role, tabs and store picker. Now the first
destination asks for the store (04a), and the picker is its own page (04b).
#445 found the picker's Search button wrapping as "Sea / rch". It no longer
wraps, at a narrower width.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: signed in store picker](445-android-session-04-signed-in-store-picker.png) | ![After, light: signed in](733-android-session-04a-signed-in-light.png) | ![After, dark: signed in](733-android-session-04a-signed-in-dark.png) |
| | ![After, light: store picker](733-android-session-04b-store-picker-light.png) | ![After, dark: store picker](733-android-session-04b-store-picker-dark.png) |

### 25 Sign out while a write waits

The emulator is in airplane mode with one check-in queued.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: sign out warning](445-android-session-25-sign-out-warning.png) | ![After, light: sign out warning](733-android-session-25-sign-out-warning-light.png) | ![After, dark: sign out warning](733-android-session-25-sign-out-warning-dark.png) |

This dialog still differs from the design system: see #1153 under
[Still different from the design system](#still-different-from-the-design-system).

## F17-03 (#433): the store and its check-in, with location refused

### 05 Store 1 chosen

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: store chosen](445-android-checkin-05-store-chosen.png) | ![After, light: store chosen](733-android-checkin-05-store-chosen-light.png) | ![After, dark: store chosen](733-android-checkin-05-store-chosen-dark.png) |

### 06 The first Check in asks for location

This is the system's own dialog.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: location dialog](445-android-checkin-06-location-dialog.png) | ![After, light: location dialog](733-android-checkin-06-location-dialog-light.png) | ![After, dark: location dialog](733-android-checkin-06-location-dialog-dark.png) |

### 07 Denied path 1: location refused

The check-in is still sent, with no position. check-ins-service's answer
replaces "Waiting to send": "No location was sent, so the check-in could not be
measured. Not verified." The status is now a badge with an icon and words.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: location denied](445-android-checkin-07-location-denied.png) | ![After, light: location denied](733-android-checkin-07-location-denied-light.png) | ![After, dark: location denied](733-android-checkin-07-location-denied-dark.png) |

### 24 Queued offline

In airplane mode the check-in waits: "Your check-in is saved on this phone. It
is sent as soon as the network allows." With the network back it was sent,
and answered unverified as in 07.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: queued offline](445-android-checkin-24-queued-offline.png) | ![After, light: queued offline](733-android-checkin-24-queued-offline-light.png) | ![After, dark: queued offline](733-android-checkin-24-queued-offline-dark.png) |

## F17-02 (#430): scanning a product, with the camera refused

### 08 Before asking

Only the first tap on Scan asks for the camera. Typing the product number and
searching the catalog are offered below it.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: before asking](445-android-scan-08-before-asking.png) | ![After, light: before asking](733-android-scan-08-before-asking-light.png) | ![After, dark: before asking](733-android-scan-08-before-asking-dark.png) |

### 09 The camera dialog

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: camera dialog](445-android-scan-09-camera-dialog.png) | ![After, light: camera dialog](733-android-scan-09-camera-dialog-light.png) | ![After, dark: camera dialog](733-android-scan-09-camera-dialog-dark.png) |

### 10 Denied path 2: camera refused

"The camera is off for this app", with Grant access, Open settings, and the
product number field.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: camera denied](445-android-scan-10-camera-denied.png) | ![After, light: camera denied](733-android-scan-10-camera-denied-light.png) | ![After, dark: camera denied](733-android-scan-10-camera-denied-dark.png) |

### 11 Product 2 looked up by number

Demo Product 2, its SKU and price from products-service, and the units at
Store 1 from stock-service. "In stock" is a badge with words, not only a color.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: manual lookup](445-android-scan-11-manual-lookup.png) | ![After, light: manual lookup](733-android-scan-11-manual-lookup-light.png) | ![After, dark: manual lookup](733-android-scan-11-manual-lookup-dark.png) |

## F17-04 (#437): customer, interests and a sale

### 12 A customer search by name

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: customer search](445-android-sale-12-customer-search.png) | ![After, light: customer search](733-android-sale-12-customer-search-light.png) | ![After, dark: customer search](733-android-sale-12-customer-search-dark.png) |

### 13 The registration form

It is now a sheet. The channels come from sales-service and are labeled in
words ("In store" for `physical_store`).

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: registration form](445-android-sale-13-registration-form.png) | ![After, light: registration form](733-android-sale-13-registration-form-light.png) | ![After, dark: registration form](733-android-sale-13-registration-form-dark.png) |

This sheet still differs from the design system: see #1154 under
[Still different from the design system](#still-different-from-the-design-system).

### 14 Tour Customer registered in store

Registration selects the new customer at once. The confirmation line
"Registered Tour Customer." of #445 is gone. Each run registers a new email,
so the tour can run again against the same database.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: registered](445-android-sale-14-registered.png) | ![After, light: registered](733-android-sale-14-registered-light.png) | ![After, dark: registered](733-android-sale-14-registered-dark.png) |

### 15 Demo Customer 3 picked from the search

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: customer selected](445-android-sale-15-customer-selected.png) | ![After, light: customer selected](733-android-sale-15-customer-selected-light.png) | ![After, dark: customer selected](733-android-sale-15-customer-selected-dark.png) |

### 16 Interests saved

Milk ticked, then saved to customer-preferences-service: "Interests saved."

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: interests saved](445-android-sale-16-interests-saved.png) | ![After, light: interests saved](733-android-sale-16-interests-saved-light.png) | ![After, dark: interests saved](733-android-sale-16-interests-saved-dark.png) |

### 18 The sale's line before confirming

Demo Product 2, added from Scan, with − and + and Remove. The sticky total
below stays in view.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: lines before confirm](445-android-sale-18-lines-before-confirm.png) | ![After, light: lines before confirm](733-android-sale-18a-lines-before-confirm-light.png) | ![After, dark: lines before confirm](733-android-sale-18a-lines-before-confirm-dark.png) |

The Promotions error panel above the line still differs: see #1155 under
[Still different from the design system](#still-different-from-the-design-system).

### 18b Check the sale (new)

Record sale now opens a review before anything is queued (#859).

| Before (#445) | After, light | After, dark |
|---|---|---|
| (no such step) | ![After, light: check the sale](733-android-sale-18b-check-the-sale-light.png) | ![After, dark: check the sale](733-android-sale-18b-check-the-sale-dark.png) |

### 19 Recorded

Confirm sale queues the sale on the phone. The worker sends it at once, and
sales-service answers "Sale 307 recorded." The phone's sales list shows it as Sent.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: recorded](445-android-sale-19-recorded.png) | ![After, light: recorded](733-android-sale-19-recorded-light.png) | ![After, dark: recorded](733-android-sale-19-recorded-dark.png) |

### 20 A refused sale

A second sale is of Demo Product 6, which Store 1 holds no stock record of.
sales-service refuses it with `no_stock_record`, and the app says so in plain
words: "This store keeps no stock of a product in the sale. Remove it from the
cart." The code and correlation id are under Details. Android asked once
whether the app may notify. The tour refused, and the bell counts the refusal.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: refused](445-android-sale-20-refused.png) | ![After, light: refused](733-android-sale-20-refused-light.png) | ![After, dark: refused](733-android-sale-20-refused-dark.png) |

The sticky total leaves little room for the sale here: see #1156 under
[Still different from the design system](#still-different-from-the-design-system).

### 21 The refused sale in the outbox

The refusal is shown as returned, with Dismiss.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: refused sale](445-android-outbox-21-refused-sale.png) | ![After, light: refused sale](733-android-outbox-21-refused-sale-light.png) | ![After, dark: refused sale](733-android-outbox-21-refused-sale-dark.png) |

### 23 After Dismiss

"Everything is synced. Nothing is waiting and nothing needs your attention."

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: empty](445-android-outbox-23-empty.png) | ![After, light: empty](733-android-outbox-23-empty-light.png) | ![After, dark: empty](733-android-outbox-23-empty-dark.png) |

## F17-05 (#441): the customer's segment and recommendations, one RECO_CLICK

### 17 Segment and recommendations

Demo Customer 3's segment from segments-service is now a label with a colored
dot ("Champion"), with its scores in words. Below it are
recommendations-service's recommendations at the customer's usual store, each
with its reason.

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: segment and recommendations](445-android-insights-17-segment-and-recommendations.png) | ![After, light: segment and recommendations](733-android-insights-17-segment-and-recommendations-light.png) | ![After, dark: segment and recommendations](733-android-insights-17-segment-and-recommendations-dark.png) |

### 22 One tap on recommendation 1

The tap opens the product on Scan and records one `RECO_CLICK`, now on
engagement v2 (#860).

| Before (#445) | After, light | After, dark |
|---|---|---|
| ![Before: before the tap](445-android-insights-22a-before-the-tap.png) | ![After, light: before the tap](733-android-insights-22a-before-the-tap-light.png) | ![After, dark: before the tap](733-android-insights-22a-before-the-tap-dark.png) |
| ![Before: recommendation opened](445-android-insights-22b-recommendation-opened.png) | ![After, light: recommendation opened](733-android-insights-22b-recommendation-opened-light.png) | ![After, dark: recommendation opened](733-android-insights-22b-recommendation-opened-dark.png) |

**One tap, counted once.** The tour reads, as the seeded analyst,
`GET /api/engagement/v2/engagement-events?customer_id=…003&type=RECO_CLICK&voided=false`.
It counted 1 before the tap, from a dry run of this tour. It counted 2 eight
seconds after the tap, and still 2 twenty seconds later, after the worker's
passes.

## Still different from the design system

Each of these has a follow-up issue. None blocks the refresh. The epic closes
with this story, and the issues stand on their own.

| Screen | What differs | Issue |
|---|---|---|
| 25 Sign out while a write waits; also the indicator definition on Indicators | A stock Material3 dialog, not a kit component. Its actions are orange-600 on `--mq-bg-active` in light (3.34:1) and orange-500 on `#383834` in dark (4.12:1), both below AA. ADR-0057 requires orange-700 for accent text. Sign out anyway is not marked as the destructive choice. | #1153 |
| 13 The registration form | A stock `ModalBottomSheet`, fully expanded, with its drag handle under the status bar in both schemes. The kit has no sheet. | #1154 |
| 18, 18b, and every sale with a line | The Promotions and discounts parts show the server's raw 404 page ("The requested URL was not found on the server…") as their message, with a Retry that cannot succeed. campaigns' `listPromotions` is still planned. The kit's error panel is for plain words. | #1155 |
| 20 A refused sale | At 375 dp the sticky total grows to about 189 dp, and leaves about 242 dp for the refusal and the cart it asks to change. | #1156 |

## Other observations, not differences from the design system

- **A wrong password offers Retry** (02). Retry sends the same refused email and
  password again. The panel is the kit's `MqErrorPanel`, shown with Retry
  whenever both fields are filled.
- **The status bar's clock differs between captures.** The captures were
  taken minutes apart, and the clock was not frozen.

## What was not captured

These are as in #445:

- **The granted paths of both permissions.** A QR code read by the camera, and
  a verified check-in from a precise fix. The emulator was given no camera
  scene and no location fix.
- **The idempotent replay of a sale whose response was lost.** It is shown by
  `SaleQueueInstrumentedTest`, not on a screen.

The screens of the other roles, and Indicators, are not part of #445's tour:

- each role's own navigation is captured at 375 dp in
  [`855-729-android-375dp.md`](855-729-android-375dp.md);
- the supervisor's Review screen is captured in
  [`1143-android-review-375dp.md`](1143-android-review-375dp.md).
