-- =========================================================
-- verify_integrity.sql
-- MOSAIQ — integrity verification: F2-07.
--
-- Not one of the three ordered scripts. Run it after them, against a
-- database that has been created, schemad and seeded:
--
--   psql -d retail -f sql/verify_integrity.sql
--
-- Every negative case below MUST fail. A case that succeeds is a hole in the
-- schema, so the expected error is named above each one and the run is only
-- clean when each produces exactly that. Each case runs in its own
-- transaction and rolls back, so the script leaves no trace and can be run
-- as many times as needed.
-- =========================================================

\set ON_ERROR_STOP off
\timing off

\echo '=============================================='
\echo 'POSITIVE CASES — these must succeed'
\echo '=============================================='

\echo ''
\echo '-- P1: a well-formed product inserts'
BEGIN;
INSERT INTO product (product_id, sku, name, category_id, list_price)
VALUES (9001, 'SKU-09001', 'Integrity probe product', 1, 10.00);
SELECT 'P1 inserted: ' || count(*)::text FROM product WHERE product_id = 9001;
ROLLBACK;

\echo ''
\echo '-- P2: updating a catalog row writes an audit entry'
BEGIN;
SELECT count(*) AS audit_rows_before FROM audit_log WHERE entity = 'product';
UPDATE product SET list_price = list_price + 1 WHERE product_id = 1;
SELECT count(*) AS audit_rows_after FROM audit_log WHERE entity = 'product';
SELECT 'P2 action recorded: ' || action FROM audit_log
 WHERE entity = 'product' ORDER BY audit_id DESC LIMIT 1;
ROLLBACK;

\echo ''
\echo '-- P3: the audit payload never carries a password hash'
BEGIN;
UPDATE app_user SET name = 'Renamed probe' WHERE user_id = '11111111-1111-1111-1111-000000000002';
SELECT 'P3 hash present in payload: ' ||
       (SELECT (data_after ? 'password_hash')::text FROM audit_log
         WHERE entity = 'app_user' ORDER BY audit_id DESC LIMIT 1);
ROLLBACK;

\echo ''
\echo '-- P4: deleting a transaction cascades to its lines'
BEGIN;
SELECT 'P4 lines before: ' || count(*)::text FROM transaction_line WHERE transaction_id = 1;
DELETE FROM transaction WHERE transaction_id = 1;
SELECT 'P4 lines after: ' || count(*)::text FROM transaction_line WHERE transaction_id = 1;
ROLLBACK;

\echo ''
\echo ''
\echo '-- P5: the administrator row still takes ordinary updates'
-- The single-administrator index constrains how many rows hold role_id = 1,
-- not what else those rows say. If this case ever fails, the rule has stopped
-- allowing the rotation F4-06 (#107) performs on the instance.
BEGIN;
UPDATE app_user SET name = 'Rotated administrator' WHERE role_id = 1;
SELECT 'P5 renamed: ' || name FROM app_user WHERE role_id = 1;
ROLLBACK;

\echo '=============================================='
\echo 'NEGATIVE CASES — every one of these must be refused'
\echo '=============================================='

\echo ''
\echo '-- N1: duplicate primary key                    [expect: 23505 unique_violation]'
BEGIN;
INSERT INTO role (role_id, code, description) VALUES (1, 'DUPLICATE', 'Duplicate primary key');
ROLLBACK;

\echo ''
\echo '-- N2: duplicate unique sku                     [expect: 23505 unique_violation]'
BEGIN;
INSERT INTO product (product_id, sku, name, category_id, list_price)
VALUES (9002, 'SKU-00001', 'Duplicate sku', 1, 10.00);
ROLLBACK;

\echo ''
\echo '-- N3: duplicate unique email                   [expect: 23505 unique_violation]'
BEGIN;
INSERT INTO app_user (role_id, name, email, password_hash)
VALUES (2, 'Duplicate email', 'admin@mosaiq-demo.com', 'x');
ROLLBACK;

\echo ''
\echo '-- N4: orphan foreign key, product -> category  [expect: 23503 foreign_key_violation]'
BEGIN;
INSERT INTO product (product_id, sku, name, category_id, list_price)
VALUES (9003, 'SKU-09003', 'Orphan category', 999, 10.00);
ROLLBACK;

\echo ''
\echo '-- N5: orphan foreign key, transaction -> customer [expect: 23503 foreign_key_violation]'
BEGIN;
INSERT INTO transaction (customer_id, store_id, channel_id, occurred_at, total)
VALUES ('99999999-9999-9999-9999-999999999999', 1, 1, now(), 100.00);
ROLLBACK;

\echo ''
\echo '-- N6: CHECK, negative price                    [expect: 23514 check_violation]'
BEGIN;
INSERT INTO product (product_id, sku, name, category_id, list_price)
VALUES (9004, 'SKU-09004', 'Negative price', 1, -1.00);
ROLLBACK;

\echo ''
\echo '-- N7: CHECK, email without @                   [expect: 23514 check_violation]'
BEGIN;
INSERT INTO app_user (role_id, name, email, password_hash)
VALUES (2, 'Bad email', 'not-an-email', 'x');
ROLLBACK;

\echo ''
\echo '-- N8: CHECK, campaign status outside the domain [expect: 23514 check_violation]'
BEGIN;
INSERT INTO campaign (campaign_id, name, segment_id, starts_on, ends_on, status)
VALUES (9005, 'Bad status', 1, DATE '2026-01-01', DATE '2026-02-01', 'PAUSED');
ROLLBACK;

\echo ''
\echo '-- N9: CHECK, RFM band outside 1..5             [expect: 23514 check_violation]'
BEGIN;
INSERT INTO segment_rule (rule_id, rule_code, r_min, r_max, f_min, f_max, m_min, m_max)
VALUES (9006, 'RULE_BAD', 9, 9, 1, 3, 1, 3);
ROLLBACK;

\echo ''
\echo '-- N10: CHECK, quantity must be positive        [expect: 23514 check_violation]'
BEGIN;
INSERT INTO transaction_line (transaction_id, product_id, quantity, unit_price)
VALUES (2, 39, 0, 10.00);
ROLLBACK;

\echo ''
\echo '-- N11: CHECK, campaign ending before it starts [expect: 23514 check_violation]'
BEGIN;
INSERT INTO campaign (campaign_id, name, segment_id, starts_on, ends_on, status)
VALUES (9007, 'Ends before it starts', 1, DATE '2026-03-01', DATE '2026-01-01', 'DRAFT');
ROLLBACK;

\echo ''
\echo '-- N12: NOT NULL, product without a name        [expect: 23502 not_null_violation]'
BEGIN;
INSERT INTO product (product_id, sku, name, category_id, list_price)
VALUES (9008, 'SKU-09008', NULL, 1, 10.00);
ROLLBACK;

\echo ''
\echo '-- N13: RESTRICT, deleting a category with products [expect: 23503 foreign_key_violation]'
-- Category 11 (Milk) is a leaf, so this exercises product -> category and not
-- the self-reference N16 covers.
BEGIN;
DELETE FROM category WHERE category_id = 11;
ROLLBACK;

\echo ''
\echo '-- N16: RESTRICT, deleting a parent category    [expect: 23503 foreign_key_violation]'
-- Category 1 (Dairy) is the parent of 11 and 12. The hierarchy is protected
-- by the same rule as the product reference, and by a different constraint.
BEGIN;
DELETE FROM category WHERE category_id = 1;
ROLLBACK;

\echo ''
\echo '-- N14: RESTRICT, deleting a customer with sales [expect: 23503 foreign_key_violation]'
BEGIN;
DELETE FROM customer WHERE customer_id = '00000000-0000-0000-0000-000000000001';
ROLLBACK;

\echo ''
\echo '-- N15: duplicate composite key                 [expect: 23505 unique_violation]'
BEGIN;
INSERT INTO inventory (store_id, product_id, quantity_on_hand) VALUES (1, 1, 5);
ROLLBACK;

\echo ''
\echo '-- N17: a second administrator, inserted directly [expect: 23505 unique_violation]'
-- The half of RN-01 that the application cannot enforce. web/services/users.py
-- refuses this too, and AGENTS.md is explicit that one half alone does not
-- count: this case is what proves the schema refuses it with the application
-- out of the picture entirely.
BEGIN;
INSERT INTO app_user (role_id, name, email, password_hash)
VALUES (1, 'Second administrator', 'second-admin@mosaiq-demo.com', 'not-a-hash');
ROLLBACK;

\echo ''
\echo '-- N18: promoting a second user to administrator [expect: 23505 unique_violation]'
-- The same rule from the other direction: the seat is taken, and an UPDATE
-- takes it no more easily than an INSERT.
BEGIN;
UPDATE app_user SET role_id = 1 WHERE email = 'user2@mosaiq-demo.com';
ROLLBACK;

\echo ''
\echo '=============================================='
\echo 'Volume check — at least 30 rows per table'
\echo 'role and channel are exempt: see sql/seed-exempt.txt'
\echo '=============================================='
-- Counted exactly. The estimate in pg_stat_user_tables is still zero
-- straight after a load, which is the trap ci.yml already documents.
SELECT table_name,
       (xpath('/row/c/text()',
              query_to_xml(format('SELECT count(*) AS c FROM public.%I', table_name),
                           false, true, '')))[1]::text::int AS exact_rows
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY table_name;
