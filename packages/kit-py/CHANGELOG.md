# Changelog

## 0.1.4 - 2026-10-21

- Read an XML request member that is required and nullable in the OpenAPI, and
  omitted from the body, as `null`, so XML and JSON agree with the contracts
  README: an absent value is omitted in XML and is null in JSON (#571).
- Read `xsi:nil="true"` on an XML request element as `null` instead of an empty
  string (#585).
- `StreamConsumer.run_forever` treats a Redis connection loss or timeout as an
  outage: one warning, backoff from 1 s doubling to 30 s, and one info line on
  recovery. Other exceptions keep their traceback (#601).
- `/openapi.yaml` and `/schema.xsd` answer 406 `not_acceptable` when `Accept`
  excludes the document's type (#589).

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
