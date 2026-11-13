# Advisory lock registry

Keys are application-wide. A new use must not overlap any one-key lock or the
first key of a two-key lock.

| Key | Form | Owner and purpose | Repository state |
|---:|---|---|---|
| 221001 | one key | Web and campaigns-service serialize `campaign_id` allocation. | Implemented in `web/db/campaigns.py` and `services/campaigns/app/rules.py` (F14-28). |
| 222001 | one key | The web serializes `experiment_id` and experiment-group allocation. | Implemented in `web/db/experiments.py`. |
| 272001 | one key | audit-trail's `api_audit.record_event_v1` serializes the domain-event hash chain, so it has one head (ADR-0072). | Reserved by [`api_audit.record_event.v1.md`](api_audit.record_event.v1.md); implemented in `sql/01_schema.sql` (F21-25). |
| 285001 | one key | The web serializes segmentation runs. | Implemented in `web/db/segments.py`. |
| 290001 | two keys: `290001`, `hashtext(customer_id::text)` | customer-preferences serializes replacement of one customer's complete preference set: `pg_advisory_xact_lock(290001, hashtext(customer_id::text))`, taken by every replace before its DELETE and INSERT. | Reserved by ADR-0034 and fixed by `contracts/customer-preferences/openapi.yaml`; implemented in `services/customer-preferences/src/lib.rs` (F14-16). The web takes no lock: it only reads both tables. |
| 293001 | two keys: `293001`, `hashtext(source id)` | The `BEFORE INSERT` guard on `public.transaction` (BOUNDARY sales, ADR-0070 section 5) serializes the two paths that can load one file: it takes `pg_advisory_xact_lock(293001, hashtext(<bare source id>))` before it looks for the same id under the other path's spelling (`svc-sales:imp:<id>` against `<id>`), so two concurrent loads of one file cannot both pass. | Implemented in `sql/01_schema.sql` (F26-08). |
| 294001 | two keys: `294001`, `hashtext(import_id::text)` | The sales import worker holds `pg_try_advisory_lock(294001, hashtext(import_id::text))` while it applies a chunk, so two workers never interleave one import and its rows are applied in file order (ADR-0070 section 3). A session-level lock on the worker's own connection. | Implemented in `services/sales/app/import_worker.py` (F26-08). |

The category hierarchy also uses the independently named lock
`hashtext('category_hierarchy')` in `sql/01_schema.sql`; it does not consume a
numeric key from this registry.

The two-key forms of 290001, 293001 and 294001 do not overlap the one-key locks 221001, 222001,
272001 and 285001: PostgreSQL keeps one-key and two-key advisory locks in separate
spaces, and no one-key lock may take the value 290001 either.
