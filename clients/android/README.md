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
- **Network security.** Release builds allow HTTPS only. Debug builds also allow cleartext to `10.0.2.2`, and nothing else (`src/debug/res/xml/network_security_config.xml`).

Every dependency version is pinned in `gradle/libs.versions.toml`.
