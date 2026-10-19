# Changelog

## 0.1.1 - 2026-10-16

- Upgrade PyJWT, lxml and Flask to patched releases in response to dependency
  advisories, and update the pytest development dependency.
- Preserve `PyJWK` metadata during verification so PyJWT also binds each token's
  algorithm to its declared key algorithm.

## 0.1.0 - 2026-10-15

- Add contract-driven Flask routing and JSON/XML handling.
- Add RS256 verification, fail-closed liveness and fixed-window limits.
- Add database attribution, outbox relay and stream consumer helpers.
- Add the build-isolated example service and public verification vectors.
