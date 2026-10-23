# #445 (F17-06): screenshots of every Android screen, against the real services

The real debug app, installed without `-g` and driven through its own UI on an
emulator. It talks to the real services behind the local stack's gateway, not
to fakes. Every refusal and answer on these screens came from a service.

The captions cite the story and its issue.

## Where it was run

| Piece | Here |
|---|---|
| Emulator | Android Emulator 37.2.12 on Windows (WHPX), AVD with `system-images;android-35;google_apis;x86_64`, 1080 × 1920 at 420 dpi. The ATD image the instrumented tests use renders black, so screenshots need a standard image |
| App | The debug build of `develop` at `3809073`, with base URL `http://10.0.2.2:8080/` |
| Stack | Fifteen services as local processes behind the generated gateway on `127.0.0.1:8080`, which Windows forwards to the emulator's `10.0.2.2`. PostgreSQL 18, loaded from the three scripts. Redis with each service's committed ACL line. MongoDB 7.0.41 with `--auth`, and engagement's `mongo/init.js` applied |
| Accounts | The seed's: `user3@mosaiq-demo.com` (STORE_MANAGER) and `user14@mosaiq-demo.com` (ANALYST), with the published demonstration password (ADR-0008) |

**To reproduce,** run from the repository root, with that stack up and the app installed:

```sh
ADB=adb uv run --with pillow python clients/android/evidence/screen_tour.py <out_dir> all
```

[`screen_tour.py`](../../clients/android/evidence/screen_tour.py) clears the
app's data, revokes its runtime permissions, and walks the screens below in
order. It finds each control by its text in a `uiautomator dump`, taps it with
`adb shell input`, and saves `screencap` at half size.

## F17-01 (#426): sign-in, the role gate, sign-out

| Screenshot | What it shows |
|---|---|
| `445-android-session-01-sign-in.png` | The sign-in form. Sign in stays disabled until both fields hold something. |
| `445-android-session-02-wrong-password.png` | A wrong password, refused by auth-service with the code `invalid_credentials` and its correlation id. The email is kept, and the password is cleared. |
| `445-android-session-03-analyst-refused.png` | An ANALYST signs in successfully and is signed out at once: "This app is for store managers and administrators. You were signed out." |
| `445-android-session-04-signed-in-store-picker.png` | The store manager signed in, with name and role. The four tabs and Sign out are shown, and the first tab asks for the store. |
| `445-android-session-25-sign-out-warning.png` | Sign out while a write waits, with the emulator in airplane mode: "Writes are still waiting. 1 floor write(s) have not been sent yet…", and Stay signed in or Sign out anyway. |

## F17-03 (#433): the store and its check-in, with location refused

| Screenshot | What it shows |
|---|---|
| `445-android-checkin-05-store-chosen.png` | Store 1 chosen from the stores-service list, with Change store and Check in. |
| `445-android-checkin-06-location-dialog.png` | The first tap on Check in, which is what asks for location: the system's own dialog, precise or approximate. |
| `445-android-checkin-07-location-denied.png` | **Denied path 1.** After "Don't allow", the app explains why it cannot verify the check-in, and still queues it without a position. check-ins-service's answer replaces the queued line: "No location was sent, so the check-in could not be measured. Not verified." |
| `445-android-checkin-24-queued-offline.png` | In airplane mode: "Check-in queued. It is sent as soon as the network allows." With the network back, it was sent and answered the same way, unverified. |

## F17-02 (#430): scanning a product, with the camera refused

| Screenshot | What it shows |
|---|---|
| `445-android-scan-08-before-asking.png` | The scan tab before any request: only the first tap on Scan asks for the camera. |
| `445-android-scan-09-camera-dialog.png` | That tap: the system's camera dialog. |
| `445-android-scan-10-camera-denied.png` | **Denied path 2.** After "Don't allow": "The camera is off for this app…", and typing the product id is offered instead. |
| `445-android-scan-11-manual-lookup.png` | Product id 2 typed in: Demo Product 2, its SKU and price from products-service, and "13 in stock at Store 1." from stock-service, with Add to sale. |

## F17-04 (#437): customer, interests and a sale queued on the phone

| Screenshot | What it shows |
|---|---|
| `445-android-sale-12-customer-search.png` | A customers-service search by name, with its results paged. |
| `445-android-sale-13-registration-form.png` | The registration form: name, email, phone, and the registration channels read from sales-service. |
| `445-android-sale-14-registered.png` | Tour Customer registered with `physical_store` ("Registered Tour Customer.") and selected, with the interests to capture. |
| `445-android-sale-15-customer-selected.png` | Demo Customer 3 picked from the search. |
| `445-android-sale-16-interests-saved.png` | Milk ticked and saved to customer-preferences-service: "Interests saved." |
| `445-android-sale-18-lines-before-confirm.png` | The sale's line, Demo Product 2 added from the scan tab, with − and + and Confirm and queue sale. |
| `445-android-sale-19-recorded.png` | Confirmed: "Sale saved on this phone", then "Sale 303 recorded." once the worker had sent it. |
| `445-android-sale-20-refused.png` | A second sale, of Demo Product 6, which Store 1 holds no stock record for. sales-service refuses it with the code `no_stock_record`, "The store has no stock record for a product.", and its correlation id. |
| `445-android-outbox-21-refused-sale.png` | **A refused sale in the outbox screen:** `SALE · 49dd6a9f`, the refusal as returned, and Dismiss. |
| `445-android-outbox-23-empty.png` | After Dismiss: "Nothing is waiting, and nothing was refused." |

## F17-05 (#441): the customer's segment and recommendations, one RECO_CLICK

| Screenshot | What it shows |
|---|---|
| `445-android-insights-17-segment-and-recommendations.png` | Demo Customer 3's open label from segments-service, `CHAMPION · R 4 F 4 M 5`. Below it are five recommendations from recommendations-service, each with the stock at the customer's usual store and the reason. |
| `445-android-insights-22a-before-the-tap.png` | The same list, computed again after the sale of Demo Product 2. The customer has now bought that product, so it is no longer recommended, and Demo Product 1 is first. |
| `445-android-insights-22b-recommendation-opened.png` | One tap on recommendation 1 opens the product on the scan tab ("10 in stock at Store 1."), and queues one `RECO_CLICK`. |

**One tap, counted once.** Read as the seeded analyst, `GET /api/engagement/v1/engagement-events?customer_id=…003` returns exactly one event for customer 3, of any type. It was recorded at the tap (`occurred_at` 05:23:25 UTC). Read again with `type=RECO_CLICK`, it is still one, also 30 s later, after the worker's passes:

```json
{"type": "RECO_CLICK", "store_id": 1, "product_id": 1,
 "customer_id": "00000000-0000-0000-0000-000000000003",
 "label_code": "CHAMPION", "rank": 1,
 "recorded_by": "11111111-1111-1111-1111-000000000003", "voided": false}
```

## What the database holds afterwards

Read as `postgres` on the stack's PostgreSQL:

- **Sales.** `transaction` 303 is customer 3's sale at Store 1: total 22.50, product 2 × 1, and `source_transaction_id` `svc-sales:…`. The refused sale left no row. Rows 301 and 302 are `StackSaleInstrumentedTest`'s two runs.
- **Check-ins.** `checkins.store_check_in` holds two Store 1 check-ins with no position and the reason `no_fix`: the denied one and the one queued offline.
- **Customers.** `customer` holds Tour Customer, with `registration_channel_id` 3 (`physical_store`).
- **Interests.** `customer_interest_category` holds Milk for customer 3.

## Found while capturing

- **The store picker's Search button wraps** (`445-android-session-04-signed-in-store-picker.png`). At 1080 px and 420 dpi, its label breaks as "Sea / rch" beside the City field. It is cosmetic, and the button works.

## What was not captured

- **The granted paths of both permissions:** a QR read by the camera, and a verified check-in from a precise fix. The emulator was given no camera scene and no location fix. Unit tests and the scan and check-in code paths cover them, but no screenshot does.
- **The idempotent replay of a sale whose response was lost.** It is shown by `SaleQueueInstrumentedTest` (`committedRequestTimesOutAndReplaysTheOriginalSaleAfterRestart`), not on a screen.
