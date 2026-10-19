# ADR-0049 — kit-rs validates XML request bodies with libxml2, after a quick-xml screen

**Status:** Proposed
**Owner:** Max
**Issue:** #420
**Supersedes:** —
**Superseded by:** —

---

## Context

[ADR-0045](0045-services-never-import-the-web-system.md) gives the six Rust services a kit, `kit-rs`, that must behave like
`kit-py`. One of those behaviours is that an XML request body is validated against the service's own XSD, and that each
violation becomes a stable `{field, code}` detail in a 422 `invalid_body` ([ADR-0035](0035-each-service-publishes-its-own-versioned-contract-and-no-two-services-share-a-resource.md)).
`kit-py` does this with lxml, which is libxml2. Its detail codes come from matching libxml2's validation messages
(`[facet 'maxLength']`, `Missing child element(s)`, `This element is not expected`, and so on).

Rust has no mature pure-Rust XSD validator. The crates that exist generate code from a schema or cover a small part of
XSD 1.0; none validates `xs:import`, facets, `minOccurs`/`maxOccurs` and the built-in types the merged contracts use.
F13-04 also asks for quick-xml, with entity expansion off and no network, as the parser that meets untrusted bytes.

The uncertainty is the cost of a C dependency in services otherwise built as one static binary. libxml2 has a long
history of parser CVEs; we limit what reaches it rather than claim it is safe.

## Decision

`kit-rs` reads every XML request body with quick-xml first and refuses, with 400 `malformed_body`, any DOCTYPE, entity
declaration, entity reference other than the five predefined ones and character references, NUL byte, or document that is
not well formed. Only a body that passes this screen and has the operation's root element is parsed by libxml2 (the
`libxml` crate, network off, recovery off) and validated against the service XSD; libxml2's messages are mapped to details
by the same rules as `kit-py`. The crate therefore needs libxml2 and libclang to build, and libxml2 at run time.

## Alternatives considered

| Alternative | Why it was rejected |
|---|---|
| Validate the XML only through the OpenAPI JSON Schema, after mapping it to a value | XML would no longer be checked against the XSD the service publishes, and a body the XSD refuses could reach a Rust handler while `kit-py` refuses it. The two kits would disagree on the same contract |
| Write an XSD subset validator inside the kit | A second XSD engine with its own messages and edge cases; it would drift from libxml2 on exactly the inputs the shared vectors do not contain, and every new facet a contract uses would be a kit release |
| Let libxml2 parse the raw body with its own options (`no_net`, no entity substitution) | libxml2 would still be the first parser of untrusted bytes, including DTDs. The quick-xml screen keeps DTD and entity handling out of the C library entirely, and is what F13-04 asks for |
| Link libxml2 statically into a distroless image | The `libxml` crate links statically only against a PIC `libxml2.a` under a non-system prefix, so every Rust build would first have to compile libxml2 itself. It can be revisited without changing the kit's behaviour |

## Consequences

**What this makes easy.**
- The two kits share one validation engine, so the same body yields the same details in both; the vectors and F13-09's
  conformance suite compare them directly.
- A contract can use any XSD construct libxml2 supports without a kit change.

**What this makes hard.**
- Every Rust service image needs `libxml2` at run time, so it is based on `debian:trixie-slim` rather than distroless, and
  its build stage needs `libxml2-dev`, `pkg-config` and `libclang-dev` (the crate generates bindings with bindgen).
- libxml2 security fixes reach a service through its base image, so a service rebuild is needed, not only a kit release.
- libxml2's schema and document types are not `Send`; the kit caches one compiled schema per worker thread.

**What must now be true elsewhere.**
- `packages/kit-rs/examples/Dockerfile` and every Rust service Dockerfile install `libxml2` in the runtime stage.
- The `Rust kit` CI job, and F13-09's Rust matrix entries, install `libxml2-dev`, `pkg-config` and `libclang-dev`.

## Compliance

- `packages/kit-rs/tests/http.rs` sends the DOCTYPE, billion-laughs, external-entity, undeclared-entity and NUL bodies and
  expects 400 `malformed_body`; `tests/contracts.rs` expects kit-py's XSD details for each facet.
- `grep -n 'recover: false' packages/kit-rs/src/contract.rs` and `grep -n 'no_net: true' packages/kit-rs/src/contract.rs`
  each print one line.
