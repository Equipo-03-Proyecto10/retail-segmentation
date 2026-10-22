# MOSAIQ back office (desktop client)

Electron and TypeScript. It talks to every service only in XML, validates each request and each
response against that service's own XSD, and signs in through auth-service
([ADR-0041](../../docs/adr/0041-android-and-electron-clients-run-different-processes-over-different-formats.md),
[ADR-0035](../../docs/adr/0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md),
[ADR-0036](../../docs/adr/0036-auth-service-issues-role-only-rs256-tokens-whose-sessions-live-in-redis.md)).
This is the F16-01 foundation: shell, secure window, preload bridge, XML transport, sign-in and
role-gated screens. Feature screens come in later stories.

## Run it

Needs Node 20.19 or later.

```sh
cd clients/desktop
npm ci
npm start        # bundles the contracts, builds, launches Electron
```

`npm start` copies `contracts/<name>/<name>.xsd` for every entry of `contracts/registry.yaml`, plus
`contracts/common/v1/common.xsd`, into `bundled/schemas/`, and writes every operation's
`x-mosaiq-roles` to `bundled/operations.json` (`scripts/bundle-contracts.mjs`). The build fails when
a service has an `openapi.yaml` but no XSD. A registry entry whose contract has not merged yet is
skipped, listed in the output, and its screens are not offered. `bundled/` and `dist/` are generated
and git-ignored.

### Against the local stack

Start the stack and the gateway (F13-08). The default base URL is the gateway,
`http://127.0.0.1:8080`, so no setting is needed. Calls go to `<base URL>/api/<service>/v1/...`.

### Against the instance

Create `settings.json` in the configuration directory (Linux `~/.config/mosaiq-desktop/`, macOS
`~/Library/Application Support/mosaiq-desktop/`, Windows `%APPDATA%\mosaiq-desktop\`):

```json
{ "baseUrl": "https://<instance host>" }
```

The base URL is never hard-coded. A malformed settings file stops the app with an error rather than
being ignored. `MOSAIQ_DESKTOP_CONFIG_DIR` overrides the directory (the window tests use it).

## Security model

- Every window: `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true`, `webSecurity` left
  on; a CSP with no remote and no inline script; `will-navigate` cancelled; every new window denied;
  every permission request denied. The one window factory is `src/main/window.ts`.
- The screens reach the network only through the named operations of `src/preload/preload.ts`
  (`getState`, `signIn`, `signOut`, `probeService`, `onStateChange`). There is no generic fetch and no
  `ipcRenderer`.
- Tokens live in the main process only, and only as sealed bytes (see Token storage below).
- A response is checked in the main process before it is parsed: its root namespace must be the called
  service's (or `common/v1` for an `Error`) and it must validate against the bundled XSD
  (`xmllint-wasm`). Otherwise it is a contract defect naming the service and the correlation id, and
  nothing of it is rendered.
- Access tokens are refreshed through one single-flight path; waiting calls are retried once.
- The client never calls `GET /v1/jwks.json`.

## Token storage

The access and refresh tokens are never held as plain strings between calls. Each is sealed as soon as
it arrives and opened into a local variable only to build the request that needs it (the
`Authorization` header, or the body of `POST /v1/sessions/refresh`).

| Mode          | When                                                                                 | Sealed with                                     | Kept across restarts                                        |
| ------------- | ------------------------------------------------------------------------------------ | ----------------------------------------------- | ----------------------------------------------------------- |
| `os_keyring`  | macOS Keychain, Windows DPAPI, or a Linux keyring (GNOME Keyring, KWallet)           | `safeStorage`                                   | Yes, in `session.bin` (mode 0600)                           |
| `memory_only` | No keyring, including Linux's `basic_text` backend, whose key is built into Chromium | AES-256-GCM under a random key made at start-up | No; nothing is written, and an old `session.bin` is deleted |

In `memory_only` mode every screen shows: "This computer has no secure keyring. Your session is kept in
memory only and ends when the app closes."

Limits: a token is plain text for the moment a request is built and sent, since the server needs the
real value, and JavaScript cannot wipe a string. In `memory_only` mode the key sits in the same process,
so sealing keeps tokens out of casual memory dumps and swap scans, not away from code running inside the
app.

## Checks

```sh
npm run lint
npm run format:check
npm run typecheck        # tsc --noEmit
npm test                 # unit tests (Vitest)
npm run test:window      # Playwright Electron tests; uses xvfb-run on a headless Linux
```

Compliance greps (both print nothing):

```sh
grep -rn "application/json" src/
grep -rlE "safeStorage|refresh_token" src/ | grep -i renderer
```
