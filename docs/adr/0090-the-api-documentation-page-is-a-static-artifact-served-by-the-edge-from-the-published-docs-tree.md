# ADR-0090 — The API documentation page is a static artifact served by the edge from the published docs tree

**Status:** Proposed
**Owner:** Raquel
**Issue:** #1192 (story F29-16)
**Supersedes:** —
**Superseded by:** —

---

## Context

The final delivery requires Swagger. Every service already serves its OpenAPI 3
contract at `/openapi.yaml`, which NGINX routes as `/api/<name>/openapi.yaml`
(ADR-0040). What is missing is a page that renders those contracts and lets a
reader try them out. The story asks for four things: the page lists the
services of `contracts/registry.yaml`, it keeps no copy of a contract, its
assets are pinned and served locally, and "Try it out" uses the reader's own
bearer token.

Three existing rules constrain where the page can live. The web calls no
service and serves no API (ADR-0045). A deployable unit that is not a service
needs an ADR (AGENTS.md). Under `/api/`, each first path segment names one
service (ADR-0035, ADR-0040). The edge's CSP allows `script-src 'self'` only,
except for the chart examples under `/docs/design-system/charts/`.

There was one judgement call. Seventeen of the nineteen contracts declare no
`servers`, and their paths start at `/v1/`. Swagger UI resolves those paths
against the host root, so an unmodified page would send "Try it out" calls to
the web application. The fix belongs in the page or in each contract, and this
record puts it in the page.

## Decision

The page is a set of static files in `docs/api-docs/`: Swagger UI
`swagger-ui-dist` 5.34.1 vendored unmodified under `vendor/` with its license
and a `SHA256SUMS`, an `index.html` with no inline script, an `init.js`, and a
`swagger-config.json` that `.github/scripts/api_docs_config.py` generates from
the registry. That file lists `/api/<name>/openapi.yaml` URLs only. NGINX
serves the page at `/api/docs/`. On the instance it comes from the copy of
`docs/` that `deploy/deploy.sh` already publishes. Locally it comes from the
gateway, with the folder mounted read-only. In both places it gets the same
security headers and CSP as the rest of the edge. `docs` becomes a reserved
`/api/` prefix that no service may take. `init.js` adds the selected service's
`/api/<name>` prefix to any same-origin path outside `/api/`. Swagger UI keeps
the token typed into "Authorize" in memory only: `persistAuthorization`,
`withCredentials` and `queryConfigEnabled` are off, and the third-party
validator is disabled.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Serve the page from the Flask web application | ADR-0045: the web consumes no service, and the page's only purpose is to call them. |
| A new `api-docs` service | A service implements one business rule (ADR-0043), and this page has none. It would also mean another container, compose project and deploy unit just to serve static files NGINX already serves. |
| Load Swagger UI from a CDN | The story forbids a runtime CDN. It would also need a new `script-src` exception and would make the page depend on a third party. |
| Bundle each contract into the page at build time | That is the copy the story rules out. A redeployed service would show its old contract until the page was rebuilt. |
| Add `servers: [{url: /api/<name>}]` to every contract | It would bump nineteen contract versions and change what Android and Electron read, only to fix one browser page. The two contracts that already declare it are left as they are, and the interceptor skips paths already under `/api/`. |
| Keep the vendored files out of git and download them at deploy time | The deploy would then depend on npm being reachable and on a pinned download nobody reviews. Vendoring with checksums makes the reviewed bytes the served bytes. |

## Consequences

**What this makes easy.** A changed contract shows up as soon as its service is
redeployed. A new registry entry shows up as soon as the generated list is
refreshed, and CI fails until it is. The page needs no process, no runtime and
no secret.

**What this makes hard.** About 2 MB of third-party minified code sits in the
repository, so upgrading it is a manual step (`docs/api-docs/README.md`). The
`/v1/` prefix fix depends on Swagger UI's `requestInterceptor` and
`specSelectors.url()`, so a major upgrade must recheck it. As with every change
to `mosaiq.conf`, the new location reaches the instance only when an operator
installs the file. The deploy reports drift but does not apply it. Because the
file sits under `docs/`, it is also reachable at `/docs/api-docs/`, which is
harmless.

**What must now be true elsewhere.** No service in `contracts/registry.yaml`
is named `docs`. Each service keeps serving its contract at `/openapi.yaml`.
The edge CSP keeps `connect-src` same-origin (through `default-src 'self'`),
which is what keeps "Try it out" on this host.

## Compliance

`tests/test_api_docs.py` checks that:

- the generated list matches the registry;
- `docs` is not a registered name;
- the page holds no contract copy;
- the vendored files match `SHA256SUMS` and the pinned version;
- `index.html` has no inline script and no external reference;
- `init.js` stores no credential and calls no third party;
- the instance location serves from the published docs copy and inherits its
  headers;
- the gateway location sends the instance's security headers.

`docker/distributed/gateway/generate.py --check` keeps the gateway's location
current.
