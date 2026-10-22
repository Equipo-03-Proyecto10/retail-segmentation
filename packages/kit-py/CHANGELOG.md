# Changelog

## 0.1.7 - 2026-10-22

- Answer 413 `payload_too_large` for a chunked body over 64 KiB. Werkzeug's
  limited stream stops at the cap without raising when there is no
  `Content-Length`, so the truncated body used to fail to parse as 400
  `malformed_body` (#594).
- Refuse `Authorization: Bearer  <token>` (extra whitespace) and a token whose
  `aud` is a list, even one containing `mosaiq-api` (#594).

## 0.1.6 - 2026-10-21

- Match a schema `pattern` with ECMA-262's end of input, as JSON Schema
  specifies and kit-rs already does. Python's `$` also matched just before a
  final newline, so `"x@example.com\n"` passed an anchored email pattern and
  `"1.00\n"` a price pattern. Such a value is now refused with 422
  `invalid_body` (`invalid_format`) (#594).

## 0.1.5 - 2026-10-21

- Read an XML request member that is a required list, and has no item
  elements, as `[]`, as kit-rs 0.1.3 does. XML cannot write an empty list any
  other way (#475).

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

## 0.1.3 - 2026-10-20

- Refuse a request body whose string, or member name, contains a NUL
  character with 422 `invalid_body` (detail code `invalid_format`).
  PostgreSQL text cannot hold U+0000, so the value used to reach the driver
  and answer 500 (#580).
- Refuse `page` above 4294967295, the bound kit-rs already applies, and any
  `page` or `size` with more digits than its maximum, with 422
  `invalid_pagination` (`out_of_range`). An unbounded page overflowed
  PostgreSQL's bigint OFFSET and answered 500 (#581).
- Skip comments and processing instructions in an XML request body, and join
  the text around them, as kit-rs does. They used to answer 500 (#582).

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
