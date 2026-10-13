# ADR-0041 — The Android and Electron clients run different processes over different formats

**Status:** Proposed
**Owner:** Marcelo
**Issue:** #398
**Supersedes:** —
**Superseded by:** —

---

## Context

The second delivery requires two clients:

| Client | Format | Requirement |
|---|---|---|
| Android | JSON only | at least four microservices and two device capabilities (camera, QR, GPS, notifications, local storage or sensors) |
| Desktop | XML only, validated against an XSD | at least four microservices, and a **different** process from the mobile one |

The team chose the technologies: Kotlin in Android Studio, and Electron.

The instructor's reference shows two desktop clients. Its Electron client is
public, reads only the books service, and validates nothing. Our scope asks for
more: authentication, four services, and XSD validation.

The two decisions that are open:

- **Which process each client runs.** "Different" must be visible in the
  demonstration, not just claimed.
- **What security baseline each starts from.** An Electron app with
  `nodeIntegration` enabled turns any XSS into code execution on the user's
  machine. An Android app with tokens in plain SharedPreferences leaks them to
  any backup.

## Decision

**Android: the in-store associate.**
- **Stack.** Kotlin, Jetpack Compose, Retrofit + OkHttp + kotlinx.serialization.
  It sends `Accept: application/json` and never parses XML.
- **Tokens** are stored encrypted. An OkHttp `Authenticator` refreshes them
  single-flight.
- **Process.** Sign in → GPS check-in at the store → scan a product QR code →
  see price and stock → find the customer → record the sale → see the
  customer's segment and recommendations.
- **Services used:** auth, catalog, inventory, customers, sales, segments and
  activity.
- **Device capabilities:** camera with QR (CameraX + ML Kit), GPS
  (FusedLocationProvider), and local storage (a Room queue that keeps a sale
  until it is sent, idempotently).

**Electron: the back office.** Its users are MARKETING, ANALYST and
INVENTORY_PLANNER.
- **Stack.** TypeScript. `contextIsolation: true`, `nodeIntegration: false`,
  `sandbox: true`, a strict CSP, and network access only through a typed
  preload bridge. Tokens are kept in `safeStorage`.
- **XML.** It sends `Accept: application/xml` and validates every response
  against `contracts/xsd/` (`xmllint-wasm`) before parsing. A response that
  fails validation is reported, never rendered.
- **Process.** Maintain the catalog and customers, review customer segments
  and recommendations, check stock per store, and read back sales and activity
  summaries.
- **Services used:** auth, catalog, customers, segments, inventory, sales and
  activity.

**Both clients** take their base URL from configuration and run against the
local Compose stack or the instance.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Both clients run the same catalog-and-sales process in different formats | Fails "a different process". The demonstration would show one flow twice |
| A public Electron client with no authentication, as in the reference | Our scope requires four services, and every one is authenticated (ADR-0036) |
| Validate XML only in tests, not at runtime | The point of the XSD is that the desktop refuses a response that drifted from the contract. A test only checks the examples |
| Kotlin Multiplatform or Flutter for both clients | The team chose Kotlin for Android and Electron for desktop. A shared codebase would also blur the "different process" requirement |
| XML on Android, JSON on the desktop | Inverts the course's requirement |
| Push notifications as the second Android capability | Needs Firebase Cloud Messaging, an external service and project. Camera/QR and GPS need nothing outside the device |

## Consequences

**What this makes easy.**
- The end-to-end flow closes naturally. The phone records a sale in JSON, the
  web shows it, and the desktop reads it back in XML.
- Every service is exercised by at least one client.

**What this makes hard.**
- **Two more toolchains** (Gradle/Android SDK, Node/Electron), each needing CI
  (F16-05, F17-06).
- **Offline sales require idempotency end to end:** a client-generated key,
  the server's key store, and a test that replays a request after a timeout.
- **GPS verification needs store coordinates** (F14-17).
- **Electron packaging for Windows and macOS is unsigned,** which triggers OS
  warnings that must be explained in the demonstration.

**What must now be true elsewhere.**
- No client story starts before the contracts it consumes have merged
  ([ADR-0035](0035-integration-contracts-are-written-before-the-code-that-serves-or-consumes-them.md)).
- `AGENTS.md` lists the client conventions: ktlint, ESLint and Prettier.
- `docs/scope-delivery-2-distributed.md` describes both processes.

## Compliance

```bash
# Android never asks for XML; Electron never asks for JSON from a service.
grep -rn "application/xml" clients/android/ && echo "Android requests XML" || true
grep -rn "application/json" clients/desktop/src/ | grep -iv "package.json" && echo "desktop requests JSON" || true
# Electron security defaults.
grep -rnE "nodeIntegration:\s*true|contextIsolation:\s*false|sandbox:\s*false" clients/desktop/ && echo "insecure BrowserWindow" || true
```
