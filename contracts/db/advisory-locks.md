# Advisory lock registry

Keys are application-wide. A new use must not overlap any one-key lock or the
first key of a two-key lock.

| Key | Form | Owner and purpose | Repository state |
|---:|---|---|---|
| 221001 | one key | Web and campaigns-service serialize `campaign_id` allocation. | Implemented in `web/db/campaigns.py`; reserved for the future service too. |
| 222001 | one key | The web serializes `experiment_id` and experiment-group allocation. | Implemented in `web/db/experiments.py`. |
| 285001 | one key | The web serializes segmentation runs. | Implemented in `web/db/segments.py`. |
| 290001 | two keys: `290001`, `hashtext(customer_id::text)` | customer-preferences serializes replacement of one customer's complete preference set. | Reserved by ADR-0034; not yet present in `web/` or `sql/`. |

The category hierarchy also uses the independently named lock
`hashtext('category_hierarchy')` in `sql/01_schema.sql`; it does not consume a
numeric key from this registry.
