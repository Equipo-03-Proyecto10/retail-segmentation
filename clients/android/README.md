# MOSAIQ Floor (Android)

The floor process of the second delivery ([ADR-0041](../../docs/adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md)): a Kotlin and Jetpack Compose app for `STORE_MANAGER` and `ADMIN`. It speaks JSON only. This scaffold (F17-01, #426) signs in, shows who is signed in, keeps the session safe and signs out. The floor screens build on it.

## Run it

Open `clients/android/` in Android Studio, or use the Gradle wrapper with Android Studio's JDK:

```bash
export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
./gradlew installDebug
```

**Against the local stack.** From the repository root, start the services:

```bash
docker network create mosaiq
docker compose -f compose.distributed.yaml up --build --wait
```

The debug build calls the gateway at `http://10.0.2.2:8080/`, which is how the emulator reaches the host. Sign in with a seeded store manager, for example `user3@mosaiq-demo.com` with the demonstration password in `sql/02_seed_30_per_table.sql`.

**Against the instance.** A release build calls `https://mosaiq.maxthecoder.online/`. Any build can be pointed elsewhere for one run with `-Pmosaiq.baseUrl=https://host/`.

## Check it

```bash
./gradlew ktlintCheck lint testDebugUnitTest
```

The unit tests run on the JVM against MockWebServer. They cover sign-in, `users/me`, the role gate, single-flight refresh, a reused refresh token, a revoked session, an unavailable auth-service and sign-out. They also guard the rules below.

ADR-0041's Android checks, run from the repository root, print nothing:

```bash
bash clients/android/check-adr41.sh
```

The instrumented tests run on a device or emulator, with real Room files and WorkManager workers:

```bash
./gradlew connectedDebugAndroidTest              # with the local stack up, add -Pandroid.testInstrumentationRunnerArguments.stack=required
./gradlew assembleDebug assembleDebugAndroidTest && bash run-denied-permission-tests.sh
```

- **The offline sale queue and the RECO_CLICK queue** start MockWebServer on the device's loopback, so they need the debug network security config described below. Robolectric does not apply that policy, so only a device run checks it.
- **One counter sale against the real services** (`StackSaleInstrumentedTest`). It signs in as the seeded store manager through the gateway at `http://10.0.2.2:8080/`, queues a sale in the outbox, lets the worker send it, and reads it back. It skips when no local stack answers, unless the run passes `stack=required`.
- **Both permission-denied paths** (`PermissionDeniedInstrumentedTest`) refuse the camera and location at the real system dialog. `connectedAndroidTest` installs with `-g`, which grants every runtime permission, so it leaves out the tests annotated `@DeniedPermissions`. `run-denied-permission-tests.sh` installs without `-g`, revokes the permissions and runs only those.

**In CI,** `.github/workflows/android.yml` (F17-06) has two jobs:

- **On a pull request that touches the client:** it validates the Gradle wrapper, runs the line above and the ADR-0041 checks, and uploads the debug APK as the `mosaiq-floor-debug-apk` artifact.
- **Nightly and on `workflow_dispatch`:** it starts the local stack from `compose.distributed.yaml`, then runs every instrumented test on an API 35 emulator. It is not a required check.

A failure there blocks only pull requests that touch the client, never a service release.

## What it promises

- **One owner per resource.** Each call goes to `<base>/api/<service>/v1/...` at the service that [`contracts/registry.yaml`](../../contracts/registry.yaml) registers the resource to. `net/Services.kt` is the only place a prefix is written, and a test checks every path against the registry.
- **JSON only.** Every request sends `Accept: application/json` and a fresh `X-Correlation-ID`. Unknown fields are ignored (`ignoreUnknownKeys`), so a field added within v1 never breaks the app. No source file mentions XML.
- **Tokens:**
  - They are encrypted with an AES-256-GCM key that stays in the Android Keystore, and stored in `noBackupFilesDir`.
  - `dataExtractionRules` and `fullBackupContent` exclude them from backups and device transfer.
  - The app has no HTTP logging, so no token reaches Logcat.
- **Refresh:**
  - **Single-flight:** one OkHttp `Authenticator` (`net/TokenAuthenticator.kt`) runs one `POST /v1/sessions/refresh` however many calls fail together, then retries each once.
  - **Terminal refusals:** `invalid_refresh`, `refresh_reused`, `session_expired` and `session_revoked` erase the tokens and send the user to sign-in with the reason.
  - **Temporary failures:** a 503 keeps the tokens and asks to try again.
- **Roles.** Only `STORE_MANAGER` and `ADMIN` get past sign-in. Any other role is signed out at once with a plain message. The gate is for usability only: the services refuse those roles anyway.
- **Errors.** Every refusal shows its `code`, its `message` and the request's correlation id.
- **Network security.** Release builds allow HTTPS only. Debug builds also allow cleartext to `10.0.2.2` and to the device's own loopback (`localhost`, `127.0.0.1`), where the instrumented tests run MockWebServer, and nothing else (`src/debug/res/xml/network_security_config.xml`).

Every dependency version is pinned in `gradle/libs.versions.toml`.

## Theme and fonts

`ui/theme/` is the only place a color, type size, shape or spacing value is written (ADR-0057); `bash clients/android/check-adr41.sh` lists any that leak out. `ThemeDriftTest` compares the palette with `docs/design-system/tokens/resolved-tokens.css`, and `ThemeContrastTest` checks the WCAG ratios of the pairs the theme defines.

`app/src/main/res/font/` holds IBM Plex Sans (one variable font) and IBM Plex Mono (400, 500, 600), licensed under the SIL Open Font License 1.1. They are the Latin subsets vendored in `docs/design-system/fonts/`, converted from woff2 to ttf, which Android cannot load as woff2. The conversion only unwraps the woff2 container; the glyphs are unchanged. The license is the one `docs/design-system/README.md` names for those files.

## Icons (Lucide, ISC)

Icons are [Lucide](https://lucide.dev) icons, vendored as vector drawables in `app/src/main/res/drawable/` named `ic_lucide_<name>.xml` (Android resource names use underscores where Lucide uses hyphens). No icon library is a dependency.

- **Source:** `lucide-static` 0.468.0, `https://unpkg.com/lucide-static@0.468.0/icons/<name>.svg`.
- **License:** ISC, Copyright (c) for portions of Lucide are held by Cole Bemis 2013-2022 as part of Feather (MIT); all other copyright (c) for Lucide are held by Lucide Contributors 2022.
- **Conversion:** only the shape geometry (`path`, `circle`, `line`, `rect`, `polyline`) is kept, in a 24dp viewport with no fill, a black 2-unit stroke and round caps and joins. The black is a placeholder: every use site tints it with `Icon(tint = ...)` through `MqIcon`.
- **Component kit:** `ui/components/` holds the `Mq*` components (button, text field, list row, badge, loading, empty state, pager, error panel, card, section, quantity stepper, snackbar host). Each has a preview in both schemes in `ComponentsPreview.kt`.

Icons in use: search, x, plus, minus, chevron-right, chevron-down, eye, eye-off, circle-alert, circle-check, info, refresh-cw, package, user-search, trash-2, clock, wifi-off, triangle-alert.
