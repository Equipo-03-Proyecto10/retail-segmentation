# Changelog

## 0.1.2 - 2026-10-19

- Add `Page.info_key`, the JSON member that holds page, size and total. It
  defaults to `page_info`, so every existing page renders as before; the users
  contract, whose v1 froze `page`, sets it (#421).

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
