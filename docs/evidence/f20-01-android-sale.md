# F20-01 (#759): a real sale from the Android release build, through the instance's HTTPS gateway

> **Status: not yet run.** This document is the procedure and the template. Every
> `‹fill: …›` is filled during the live run, and the run needs explicit approval
> from the team first. It creates an account on the instance, records a real sale
> and decrements real `inventory` rows. Until then, nothing below is evidence.

The acceptance criteria of #759, and where each is answered:

| Criterion | Section |
|---|---|
| A `STORE_MANAGER` created on the instance through users-service (ADR-0008 keeps the seeded accounts deactivated) signs in from a release build calling `https://mosaiq.maxthecoder.online/` | [The account](#1-the-account) and [the build](#2-build-the-release-apk) |
| The HTTPS request of the sale (method, path, headers with the token masked, body) and the 201 response with its `X-Correlation-ID` | [The sale over HTTPS](#4-the-sale-over-https) |
| The `transaction`, `transaction_line`, `sales.sale_capture` and decremented `inventory` rows, read with parameterized queries | [The rows](#5-the-rows) |
| 403 `forbidden_role` outside the role's `x-mosaiq-roles`, and 401 `invalid_token` with no token | [The refusals](#6-the-refusals) |
| The deployed version of every service involved | [The versions](#7-the-versions) |

Governing decisions: [ADR-0008](../adr/0008-the-instance-keeps-the-demonstration-accounts-deactivated.md),
[ADR-0039](../adr/0039-sales-service-records-a-sale-all-or-nothing-consuming-stock-through-the-stock-contract.md),
[ADR-0041](../adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md).

## Before the run

Each of these must hold, and the record says when it was checked.

1. **PR #989 (stock movements) is merged**, and if it was released, stock-service
   is deployed at that release. The sale decrements the same `inventory` rows
   that #989's movements write, so the "before" snapshot is taken after it.
2. **Store assignments.** On `develop` at the time of writing, no service narrows
   by store yet: `/v1/store-assignments/{user_id}` is `x-mosaiq-status: planned`
   in `contracts/users/openapi.yaml`, and sales-service does not read
   `api_users.store_assignment_v1`. If F19-05 to F19-07 (ADR-0060) have been
   deployed by the time of the run, an assignment to the sale's store is also
   needed (step 1.4), or the sale is 403 `forbidden_store`.
3. **The instance answers.** Every service involved is ready through Cloudflare:

   ```sh
   for s in auth users stores products stock customers sales; do
     printf '%-10s %s\n' "$s" "$(curl -s -o /dev/null -w '%{http_code}' \
       "https://mosaiq.maxthecoder.online/api/$s/health/ready")"
   done   # every line 204
   ```

4. **Tools on the workstation:** Android SDK build-tools (`zipalign`,
   `apksigner`), the emulator with the system image
   `system-images;android-33;google_apis;x86_64` (not `google_apis_playstore`,
   which cannot be rooted), mitmproxy 10.1 or later, `jq`, and SSH
   access to the instance as a sudoer (the queries run there, with its psql).

Nothing in the run needs a physical phone. A phone without root cannot be
captured: the release build trusts only the system's certificate authorities
(`network_security_config.xml`), and a capture needs one more on that store. A
physical phone may still be used for a second, uncaptured run if one is wanted.

## The procedure

### 1. Create the account

The administrator is the real account F4-06 provisioned; its password was never
in this repository. Sign in as it, then create the store manager through
users-service. Passwords are read from the terminal, never typed on the command
line or kept in a file.

```sh
BASE=https://mosaiq.maxthecoder.online
read -r  -p 'Administrator email: ' ADMIN_EMAIL
read -rs -p 'Administrator password: ' ADMIN_PASSWORD; echo
ADMIN_TOKEN=$(jq -n --arg e "$ADMIN_EMAIL" --arg p "$ADMIN_PASSWORD" \
    '{email:$e, password:$p, device_label:"F20-01 account setup"}' |
  curl -sS -X POST "$BASE/api/auth/v1/sessions" \
    -H 'Content-Type: application/json' -H 'Accept: application/json' \
    --data-binary @- | jq -r .access_token)
unset ADMIN_PASSWORD

# 1.2 A password generated here and kept only in the team's password manager.
MANAGER_PASSWORD=$(openssl rand -base64 18)
read -r -p 'New store manager email: ' MANAGER_EMAIL

# 1.3 The account (201, contracts/users/openapi.yaml createUser)
jq -n --arg e "$MANAGER_EMAIL" --arg p "$MANAGER_PASSWORD" \
    '{name:"F20-01 Store Manager", email:$e, role_code:"STORE_MANAGER", password:$p}' |
  curl -sS -i -X POST "$BASE/api/users/v1/users" \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -H 'Content-Type: application/json' -H 'Accept: application/json' \
    -H "X-Correlation-ID: f20-01-create-user" --data-binary @-
```

1.4 Only if store assignments are deployed (see above):
`PUT /api/users/v1/store-assignments/{user_id}` with `{"store_ids": [<store>]}`.

1.5 Sign the administrator out:
`curl -sS -X DELETE "$BASE/api/auth/v1/sessions/current" -H "Authorization: Bearer $ADMIN_TOKEN"`,
then `unset ADMIN_TOKEN`. Give `MANAGER_PASSWORD` to the password manager and
`unset` it.

On the instance, read-only, the account as PostgreSQL holds it
([`sale_evidence.sql`](../../clients/android/evidence/sale_evidence.sql) is
copied there first, with `scp`):

```sh
sudo -u postgres psql -X -d retail -v ON_ERROR_STOP=1 \
  -v email='<the new email>' -f sale_evidence.sql
```

### 2. Build the release APK

From `clients/android/`, on the commit the record names. No
`-Pmosaiq.baseUrl`: the release build's default is the instance.

```sh
git rev-parse --short HEAD
./gradlew --no-daemon clean :app:assembleRelease
# The release type has no signingConfig, so Gradle leaves the APK unsigned. Sign
# it with a key made for this run, kept outside the repository.
keytool -genkeypair -v -keystore ~/.android/f20-01-release.jks -alias f20-01 \
  -keyalg RSA -keysize 4096 -validity 30 -dname 'CN=MOSAIQ F20-01 evidence'
BT="$ANDROID_HOME/build-tools/<version>"
"$BT/zipalign" -f -p 4 app/build/outputs/apk/release/app-release-unsigned.apk \
  ~/f20-01/mosaiq-floor-release.apk
"$BT/apksigner" sign --ks ~/.android/f20-01-release.jks --ks-key-alias f20-01 \
  ~/f20-01/mosaiq-floor-release.apk
"$BT/apksigner" verify --print-certs ~/f20-01/mosaiq-floor-release.apk
sha256sum ~/f20-01/mosaiq-floor-release.apk
# The base URL compiled into BuildConfig:
unzip -p ~/f20-01/mosaiq-floor-release.apk 'classes*.dex' | strings |
  grep -F 'https://mosaiq.maxthecoder.online/'
```

### 3. Prepare the device and the capture

The app has no HTTP logging, by design (`net/Network.kt`, README "Tokens"), and
the release build is not changed for this run. The request is captured outside
the app instead: mitmproxy on the workstation, as the emulator's proxy, with its
certificate authority added to the emulator's system store. The phone still
talks to `mosaiq.maxthecoder.online` over HTTPS, through Cloudflare and the
instance's NGINX. The only difference is the proxy's TLS hop in front of it.

```sh
# 3.1 An AVD that can be rooted (API 33, google_apis), started writable.
avdmanager create avd -n f20-01 -k 'system-images;android-33;google_apis;x86_64'
mitmdump --listen-host 127.0.0.1 --listen-port 8081 \
  --set hardump="$HOME/f20-01/capture.har" &      # writes the HAR on exit
emulator -avd f20-01 -writable-system -http-proxy http://127.0.0.1:8081 &

# 3.2 mitmproxy's CA into /system (once per AVD; ~/.mitmproxy exists after the
# first mitmdump start).
HASH=$(openssl x509 -inform PEM -subject_hash_old \
  -in ~/.mitmproxy/mitmproxy-ca-cert.pem | head -1)
cp ~/.mitmproxy/mitmproxy-ca-cert.pem "/tmp/$HASH.0"
adb root && adb shell avbctl disable-verification && adb reboot
adb wait-for-device && adb root && adb remount
adb push "/tmp/$HASH.0" /system/etc/security/cacerts/
adb shell chmod 644 "/system/etc/security/cacerts/$HASH.0" && adb reboot

# 3.3 Install the release APK (uninstall any debug build first: same id,
# another signature) and show it is not debuggable.
adb uninstall online.maxthecoder.mosaiq.floor || true
adb install ~/f20-01/mosaiq-floor-release.apk
adb shell dumpsys package online.maxthecoder.mosaiq.floor |
  grep -E 'versionName|pkgFlags'      # no DEBUGGABLE
```

### 4. Record the sale on the phone

1. Choose a store and one or two active products with stock there, and take the
   "before" snapshot on the instance:

   ```sh
   sudo -u postgres psql -X -d retail -v ON_ERROR_STOP=1 \
     -v store_id=<store> -v product_ids='{<id>,<id>}' -f sale_evidence.sql
   ```

2. On the emulator: sign in as the new store manager, pick that store, find a
   customer in **Sale**, add the products (typed by id; a scan also queues a
   `QR_SCAN` engagement event), and confirm. Take a screenshot of the recorded
   sale (`f20-01-android-sale-recorded.png`).
3. Stop mitmdump (Ctrl-C) so it writes the HAR, then render the exchanges with
   every token masked:

   ```sh
   python clients/android/evidence/sale_evidence.py har ~/f20-01/capture.har \
     --path /api/auth/v1/sessions --path /api/users/v1/users/me \
     --path /api/sales/v1/channels --path /api/sales/v1/sales
   ```

   It exits 3, printing nothing, if anything shaped like a token survives. The
   raw HAR holds live tokens: it stays outside the repository and is deleted
   once this document is filled. `*.har` is gitignored.

### 5. Read the rows on the instance

```sh
sudo -u postgres psql -X -d retail -v ON_ERROR_STOP=1 \
  -v sale_id=<sale_id of the 201> -v idempotency_key=<its Idempotency-Key> \
  -f sale_evidence.sql
```

The script runs inside `BEGIN READ ONLY … ROLLBACK`, and every value is a bind
parameter (`\bind`), never text in a statement.

### 6. Make the refused calls

The same account, signed in again from the workstation:

```sh
MOSAIQ_F20_BASE_URL=https://mosaiq.maxthecoder.online \
MOSAIQ_F20_EMAIL='<the new email>' \
  python clients/android/evidence/sale_evidence.py refusals
```

It asks for the password, then signs in, calls `GET /api/users/v1/users`
(`x-mosaiq-roles: [ADMIN, AUDITOR]`) with the token, calls
`POST /api/sales/v1/sales` with no token, and signs out. The services refuse
both calls before reading a body (`mosaiq_kit` checks the token and role
first), so neither writes. It exits 0 only when both refusals hold.

### 7. Read the versions on the instance

Read-only:

```sh
for s in auth users stores products stock customers sales; do
  printf '%-10s %s  %s\n' "$s" \
    "$(readlink "/opt/mosaiq/services/$s/current")" \
    "$(curl -s "https://mosaiq.maxthecoder.online/api/$s/openapi.yaml" |
       sed -n 's/^  version: //p' | head -1)"
done
sudo docker ps --format '{{.Names}}\t{{.Image}}' | sort
sudo git -C /opt/mosaiq/current rev-parse --short HEAD
```

Add a row for every other service whose path appears in the HAR
(`customer-preferences`, `segments`, `recommendations`, `engagement`,
`check-ins`), if the run reached one.

### 8. Afterwards

- The account stays active for F20-02 (#760), which reads this sale from
  Electron. Once #760 is recorded, deactivate it through
  `POST /api/users/v1/users/{user_id}/deactivation` (RN-04: deactivated, never
  deleted).
- Delete `~/f20-01/capture.har`. Remove the mitmproxy CA from the AVD, or delete
  the AVD. Keep or destroy `~/.android/f20-01-release.jks`; it signs nothing
  else.

---

## The record

*Everything below is filled during the live run.*

### Where it ran

| Piece | Here |
|---|---|
| Date and time (UTC) | ‹fill: start – end› |
| App | MOSAIQ Floor ‹fill: versionName›, release build of `develop` at ‹fill: commit›, APK SHA-256 ‹fill›, signed with a run-only key (certificate SHA-256 ‹fill›) |
| Base URL compiled in | ‹fill: the `strings` line from step 2› |
| Device | Android Emulator ‹fill: version›, `system-images;android-33;google_apis;x86_64`, `pkgFlags` without `DEBUGGABLE` |
| Capture | mitmproxy ‹fill: version› as the emulator's HTTP proxy, its CA in `/system/etc/security/cacerts/` |
| Account | ‹fill: email›, `STORE_MANAGER`, created by users-service at ‹fill: time› (correlation id `f20-01-create-user`) |
| Approval | ‹fill: who approved the live run, and where› |

### 1. The account

```text
‹fill: the 201 of POST /api/users/v1/users, password absent from the body›
```

```text
‹fill: sale_evidence.sql with -v email=…: user_id, email, STORE_MANAGER, is_active t›
```

### 2. Sign-in from the phone

‹fill: the `POST /api/auth/v1/sessions` and `GET /api/users/v1/users/me`
exchanges from `sale_evidence.py har`, with the tokens masked.›

### 3. Inventory before

```text
‹fill: sale_evidence.sql with -v store_id=… -v product_ids=…›
```

### 4. The sale over HTTPS

![The sale recorded on the phone](f20-01-android-sale-recorded.png)

*‹fill: caption — store, customer, lines and total as the phone shows them›*

Request:

```http
‹fill: POST /api/sales/v1/sales HTTP/2.0
host: mosaiq.maxthecoder.online
authorization: Bearer <masked #n>
accept: application/json
x-correlation-id: …
idempotency-key: …
content-type: application/json; charset=UTF-8

{ "customer_id": …, "store_id": …, "channel_id": …, "occurred_at": …, "lines": [ … ] }›
```

Response:

```http
‹fill: HTTP/2.0 201
x-correlation-id: … (the request's own)
location: /v1/sales/…
strict-transport-security: max-age=31536000; includeSubDomains
cf-cache-status: DYNAMIC

{ "sale_id": …, "total": …, "lines": [ … ] }›
```

The `authorization` fingerprint is the same as the `access_token` fingerprint
in the sign-in response: ‹fill: yes/no›.

### 5. The rows

```text
‹fill: sale_evidence.sql with -v sale_id=… -v idempotency_key=…:
  transaction (key_matches t, data_origin OBSERVED),
  transaction_line,
  sales.sale_capture (recorded_by = the new account, STORE_MANAGER),
  inventory after,
  the outbox row or none›
```

| Product | On hand before | Sold | On hand after |
|---|---|---|---|
| ‹fill› | ‹fill› | ‹fill› | ‹fill› |

### 6. The refusals

```text
‹fill: the summary lines and exchanges printed by sale_evidence.py refusals:
  GET /api/users/v1/users with the store manager's token -> 403 forbidden_role
  POST /api/sales/v1/sales without a token               -> 401 invalid_token›
```

### 7. The versions

| Service | Release on the instance (`current`) | Image | Contract (`info.version`) | Reached by this run |
|---|---|---|---|---|
| auth | ‹fill› | ‹fill› | ‹fill› | sign-in, sign-out |
| users | ‹fill› | ‹fill› | ‹fill› | account, `users/me`, the 403 |
| stores | ‹fill› | ‹fill› | ‹fill› | store picker |
| products | ‹fill› | ‹fill› | ‹fill› | product lookup |
| stock | ‹fill› | ‹fill› | ‹fill› | stock level read; `api_stock.consume_v1` in the sale's transaction |
| customers | ‹fill› | ‹fill› | ‹fill› | customer search |
| sales | ‹fill› | ‹fill› | ‹fill› | channels, the sale, the 401 |
| web (`/opt/mosaiq/current`) | ‹fill: commit› | — | — | not called; named for the schema it applied |

### Found on the way

‹fill, or "Nothing."›

### What was not done

‹fill›
