# Changelog

Releases are annotated tags `kit-rs/vX.Y.Z`. Each entry names the kit-py
behaviour it matches.

## Unreleased (0.1.0)

### Added

- Error envelope in JSON and `common/v1` XML, byte-identical to kit-py.
- Representations, collections and pages, rendered as kit-py renders them:
  ASCII-escaped JSON with Python float formatting, lxml-identical XML,
  `money()` amounts, UTC timestamps.
- `X-Correlation-ID` echoed when it is a plain token, generated otherwise.
- The 64 KiB request cap (413) before anything else, werkzeug-equivalent
  `Accept` negotiation (406), the request media type (415) and pagination
  (422 `invalid_pagination`).
- Meta routes `/health/live`, `/health/ready`, `/openapi.yaml`,
  `/schema.xsd` and frozen `/schema.v<N>.xsd`.
- Routing errors (404, 405 with `Allow`) and panics (500) in the envelope.
- Logging with request fields and credential redaction.
