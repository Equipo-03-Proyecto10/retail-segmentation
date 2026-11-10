-- F20-01 (#759): the PostgreSQL evidence for one sale recorded from the
-- Android release build through https://mosaiq.maxthecoder.online/.
--
-- Read-only: everything runs inside BEGIN READ ONLY and ends in ROLLBACK.
-- Every value is a bind parameter ($1, $2) sent with \bind, never text spliced
-- into a statement, so a value cannot change what a query does. Values arrive
-- as psql variables (ids, an email, a UUID, an array such as '{1,3}': none
-- holds a space), and the variables that are set choose what is printed:
--
--   email                  the account: role code, active, created
--   store_id, product_ids  inventory before the sale, e.g. product_ids='{1,3}'
--   sale_id, idempotency_key
--                          the sale: transaction, transaction_line,
--                          sales.sale_capture with who recorded it, inventory
--                          after, and the sale's outbox row if not yet relayed
--
-- On the instance, as the owner over the local socket (peer):
--
--   sudo -u postgres psql -X -d retail -v ON_ERROR_STOP=1 \
--     -v email=<new store manager> -v store_id=<id> -v product_ids='{<ids>}' \
--     -f sale_evidence.sql
--   sudo -u postgres psql -X -d retail -v ON_ERROR_STOP=1 \
--     -v sale_id=<sale_id from the 201> -v idempotency_key=<Idempotency-Key> \
--     -f sale_evidence.sql
--
-- The procedure is docs/evidence/f20-01-android-sale.md.

\set ECHO queries
\pset null '(null)'
BEGIN READ ONLY;

\if :{?email}
\echo '-- The account, by email:' :'email'
SELECT u.user_id, u.email, r.code AS role_code, u.is_active, u.created_at
  FROM app_user AS u
  JOIN role AS r USING (role_id)
 WHERE lower(u.email) = lower($1)
\bind :email
\g
\endif

\if :{?store_id}
\echo '-- Inventory before the sale, store' :'store_id' 'products' :'product_ids'
SELECT i.store_id, i.product_id, p.sku, p.list_price, p.is_active,
       i.quantity_on_hand, i.updated_at
  FROM inventory AS i
  JOIN product AS p USING (product_id)
 WHERE i.store_id = $1::smallint
   AND i.product_id = ANY ($2::int[])
 ORDER BY i.product_id
\bind :store_id :product_ids
\g
\endif

\if :{?sale_id}
\echo '-- The transaction, sale' :'sale_id' 'with Idempotency-Key' :'idempotency_key'
SELECT t.transaction_id, t.source_transaction_id,
       t.source_transaction_id = 'svc-sales:' || lower($2) AS key_matches,
       t.customer_id, t.store_id, t.channel_id, t.occurred_at, t.total,
       t.data_origin
  FROM transaction AS t
 WHERE t.transaction_id = $1::bigint
\bind :sale_id :idempotency_key
\g

\echo '-- Its lines'
SELECT l.transaction_id, l.product_id, l.quantity, l.unit_price,
       l.quantity * l.unit_price AS line_total
  FROM transaction_line AS l
 WHERE l.transaction_id = $1::bigint
 ORDER BY l.product_id
\bind :sale_id
\g

\echo '-- Its capture row, and who recorded it'
SELECT c.transaction_id, encode(c.request_sha256, 'hex') AS request_sha256,
       c.recorded_by, u.email, r.code AS role_code, c.captured_at
  FROM sales.sale_capture AS c
  JOIN app_user AS u ON u.user_id = c.recorded_by
  JOIN role AS r USING (role_id)
 WHERE c.transaction_id = $1::bigint
\bind :sale_id
\g

\echo '-- Inventory after the sale, for the sale''s store and products'
SELECT i.store_id, i.product_id, l.quantity AS sold, i.quantity_on_hand,
       i.updated_at
  FROM transaction AS t
  JOIN transaction_line AS l USING (transaction_id)
  JOIN inventory AS i
    ON i.store_id = t.store_id AND i.product_id = l.product_id
 WHERE t.transaction_id = $1::bigint
 ORDER BY i.product_id
\bind :sale_id
\g

\echo '-- The sale''s outbox row; none once the relay has published and removed it'
SELECT o.stream, o.event_type, o.entity_id, o.occurred_at, o.relayed_at
  FROM sales_events.outbox AS o
 WHERE o.entity_id = $1
\bind :sale_id
\g
\endif

ROLLBACK;
