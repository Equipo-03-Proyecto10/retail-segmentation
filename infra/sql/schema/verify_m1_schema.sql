-- =============================================================================
-- S0-04 acceptance verification. Every check below corresponds to an acceptance
-- criterion. Run with: psql -d <db> -f verify_m1_schema.sql
-- Expected output: every line reports PASS.
-- =============================================================================

\set ON_ERROR_STOP off
\pset pager off

-- ---------------------------------------------------------------- fixtures ---
INSERT INTO store (code, name, region, city) VALUES
    ('MTY01','Monterrey Centro','Noreste','Monterrey'),
    ('GDL01','Guadalajara Sur','Occidente','Guadalajara');

INSERT INTO category (code, name) VALUES ('BEV','Beverages'), ('SNK','Snacks');

INSERT INTO customer (source_system, external_customer_id, full_name) VALUES
    ('seed','C0001','Customer One'),
    ('seed','C0002','Customer Two'),
    ('seed','C0003','Customer Three');

INSERT INTO user_account (email, password_hash, full_name) VALUES
    ('analyst@team03.mx','$argon2id$dummy','Commercial Analyst');

INSERT INTO product (sku, name, category_id, unit_price)
SELECT 'SKU-001','Cola 600ml', id, 22.50 FROM category WHERE code='BEV';

-- two RFM runs over consecutive half-open windows
INSERT INTO rfm_run (analysis_window_start, analysis_window_end, reference_date,
                     code_version, correlation_id, status, completed_at)
VALUES ('2025-01-01','2025-07-01','2025-07-01','abc1234', gen_random_uuid(),
        'succeeded','2025-07-01 12:00:00+00'),
       ('2025-07-01','2026-01-01','2026-01-01','abc1234', gen_random_uuid(),
        'succeeded','2026-01-01 12:00:00+00');

-- snapshots: customer 1 downgrades, customer 2 stays, customer 3 upgrades
INSERT INTO customer_rfm_snapshot
    (rfm_run_id, customer_id, recency_days, frequency, monetary,
     r_score, f_score, m_score, rfm_cell,
     top_category_id, dominant_channel_code, dominant_store_id)
SELECT r.id, c.id,
       CASE WHEN r.analysis_window_start='2025-01-01' THEN 10 ELSE 200 END,
       CASE WHEN r.analysis_window_start='2025-01-01' THEN 12  ELSE 2 END,
       CASE WHEN r.analysis_window_start='2025-01-01' THEN 9000 ELSE 800 END,
       5,5,5,'555',
       (SELECT id FROM category WHERE code = CASE WHEN r.analysis_window_start='2025-01-01' THEN 'BEV' ELSE 'SNK' END),
       CASE WHEN r.analysis_window_start='2025-01-01' THEN 'in_store' ELSE 'mobile_app' END,
       (SELECT id FROM store WHERE code='MTY01')
FROM rfm_run r CROSS JOIN customer c WHERE c.external_customer_id='C0001';

INSERT INTO customer_rfm_snapshot
    (rfm_run_id, customer_id, recency_days, frequency, monetary,
     r_score, f_score, m_score, rfm_cell)
SELECT r.id, c.id, 15, 10, 7000, 5,5,5,'555'
FROM rfm_run r CROSS JOIN customer c
WHERE c.external_customer_id IN ('C0002','C0003');

-- two production runs plus one candidate run over the second window
INSERT INTO segmentation_model_run
    (rfm_run_id, algorithm_version, library_version, code_version, k, random_seed,
     feature_set_version, scaler_kind, scaler_state, purpose, status,
     correlation_id, completed_at, promoted_at)
SELECT id, '1.0','scikit-learn 1.5.1','abc1234', 5, 42, 'fs-1','standard',
       '{"mean":[0,0,0],"scale":[1,1,1]}'::jsonb, 'production','succeeded',
       gen_random_uuid(), completed_at, completed_at
FROM rfm_run ORDER BY analysis_window_start;

INSERT INTO segmentation_model_run
    (rfm_run_id, algorithm_version, library_version, code_version, k, random_seed,
     feature_set_version, scaler_kind, scaler_state, purpose, status,
     correlation_id, completed_at)
SELECT id, '1.0','scikit-learn 1.5.1','abc1234', 8, 42, 'fs-1','standard',
       '{"mean":[0,0,0],"scale":[1,1,1]}'::jsonb, 'candidate','succeeded',
       gen_random_uuid(), completed_at
FROM rfm_run ORDER BY analysis_window_start DESC LIMIT 1;

-- one labelled segment per run
INSERT INTO segment (model_run_id, cluster_index, label_code, centroid_scaled)
SELECT id, 0,
       CASE WHEN purpose='candidate' THEN 'loyal'
            WHEN promoted_at = '2025-07-01 12:00:00+00' THEN 'champions'
            ELSE 'at_risk' END,
       '{"r":0.9,"f":0.8,"m":0.9}'::jsonb
FROM segmentation_model_run;

-- ---------------------------------------------------------------------------
\echo '=== CHECK 1: first authoritative assignment inserts cleanly'
INSERT INTO customer_segment_assignment
    (customer_id, segment_id, model_run_id, rfm_snapshot_id, valid_from)
SELECT c.id, s.id, s.model_run_id, sn.id, '2025-07-01 12:00:00+00'
FROM   customer c
JOIN   segmentation_model_run r ON r.promoted_at = '2025-07-01 12:00:00+00'
JOIN   segment s ON s.model_run_id = r.id
JOIN   customer_rfm_snapshot sn ON sn.rfm_run_id = r.rfm_run_id AND sn.customer_id = c.id;
\echo '--> expected: INSERT 0 3'

\echo ''
\echo '=== CHECK 2: a second OPEN authoritative assignment must be REJECTED'
\echo '    (acceptance criterion: mutable assignment fails at the database level)'
BEGIN;
INSERT INTO customer_segment_assignment
    (customer_id, segment_id, model_run_id, rfm_snapshot_id, valid_from)
SELECT a.customer_id, a.segment_id, a.model_run_id, a.rfm_snapshot_id,
       '2026-01-01 12:00:00+00'
FROM customer_segment_assignment a LIMIT 1;
ROLLBACK;
\echo '--> expected: ERROR ... exclusion constraint, raised AT STATEMENT TIME.'
\echo '    If this reports INSERT 0 1 the constraint was declared INITIALLY'
\echo '    DEFERRED and the application has lost fail-fast behaviour.'

\echo ''
\echo '=== CHECK 3: OVERLAPPING CLOSED intervals must also be REJECTED'
\echo '    (the originally specified partial unique index would have allowed this)'
BEGIN;
INSERT INTO customer_segment_assignment
    (customer_id, segment_id, model_run_id, rfm_snapshot_id, valid_from, valid_to)
SELECT a.customer_id, a.segment_id, a.model_run_id, a.rfm_snapshot_id,
       '2025-09-01 00:00:00+00', '2025-12-01 00:00:00+00'
FROM customer_segment_assignment a LIMIT 1;
ROLLBACK;
\echo '--> expected: ERROR ... exclusion constraint (statement time)'

\echo ''
\echo '=== CHECK 4: a CANDIDATE run may coexist with production (model comparison)'
INSERT INTO customer_segment_assignment
    (customer_id, segment_id, model_run_id, rfm_snapshot_id, valid_from, is_authoritative)
SELECT c.id, s.id, s.model_run_id, sn.id, '2026-01-01 12:00:00+00', false
FROM   customer c
JOIN   segmentation_model_run r ON r.purpose='candidate'
JOIN   segment s ON s.model_run_id = r.id
JOIN   customer_rfm_snapshot sn ON sn.rfm_run_id = r.rfm_run_id AND sn.customer_id = c.id;
\echo '--> expected: INSERT 0 3  (this is what the original index made impossible)'

\echo ''
\echo '=== CHECK 5: opt-in deferral permits insert-then-close ordering'
BEGIN;
SET CONSTRAINTS ex_customer_segment_assignment_no_overlap DEFERRED;
INSERT INTO customer_segment_assignment
    (customer_id, segment_id, model_run_id, rfm_snapshot_id, valid_from)
SELECT c.id, s.id, s.model_run_id, sn.id, '2026-01-01 12:00:00+00'
FROM   customer c
JOIN   segmentation_model_run r ON r.purpose='production' AND r.promoted_at='2026-01-01 12:00:00+00'
JOIN   segment s ON s.model_run_id = r.id
JOIN   customer_rfm_snapshot sn ON sn.rfm_run_id = r.rfm_run_id AND sn.customer_id = c.id;

UPDATE customer_segment_assignment a
SET    valid_to = '2026-01-01 12:00:00+00',
       closed_by_run_id = (SELECT id FROM segmentation_model_run
                           WHERE promoted_at='2026-01-01 12:00:00+00')
WHERE  a.valid_to IS NULL
  AND  a.is_authoritative
  AND  a.valid_from = '2025-07-01 12:00:00+00';
COMMIT;
\echo '--> expected: COMMIT (no error). Order of operations must not matter.'

\echo ''
\echo '=== CHECK 6: point-in-time lookup returns exactly ONE segment per customer'
SELECT 'as of 2025-10-01' AS asof, count(*) AS rows,
       CASE WHEN count(*) = 3 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   customer_segment_assignment a
WHERE  a.is_authoritative AND a.superseded_at IS NULL
  AND  a.valid_from <= '2025-10-01' AND (a.valid_to IS NULL OR a.valid_to > '2025-10-01');

SELECT 'as of 2026-03-01' AS asof, count(*) AS rows,
       CASE WHEN count(*) = 3 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   customer_segment_assignment a
WHERE  a.is_authoritative AND a.superseded_at IS NULL
  AND  a.valid_from <= '2026-03-01' AND (a.valid_to IS NULL OR a.valid_to > '2026-03-01');

\echo ''
\echo '=== CHECK 7: migration report counts LABEL changes, not segment id changes'
SELECT from_label_code, to_label_code, direction, count(*) AS customers
FROM   v_segment_migration
GROUP  BY 1,2,3;
\echo '--> expected: 3 customers champions -> at_risk, direction downgrade.'
\echo '    A segment_id comparison would have reported migration for every'
\echo '    customer on every run, because segments are run-scoped.'

\echo ''
\echo '=== CHECK 8: behaviour delta explains WHY the customer moved'
SELECT customer_id, frequency_delta, monetary_delta,
       category_shifted, channel_switched, store_switched
FROM   v_customer_behavior_delta ORDER BY customer_id;
\echo '--> expected: customer 1 shows category_shifted and channel_switched true.'

\echo ''
\echo '=== CHECK 9: audit_log is append-only'
INSERT INTO audit_log (action, entity_type, entity_id, source_component, correlation_id)
VALUES ('execute_run','segmentation_model_run','1','web', gen_random_uuid());
BEGIN;
UPDATE audit_log SET action = 'tampered';
ROLLBACK;
\echo '--> expected: ERROR audit_log is append-only'
BEGIN;
DELETE FROM audit_log;
ROLLBACK;
\echo '--> expected: ERROR audit_log is append-only'

\echo ''
\echo '=== CHECK 10: a return must not carry a positive amount'
BEGIN;
INSERT INTO sales_transaction (source_system, channel_code, transaction_type,
                               occurred_at, gross_amount, net_amount, store_id)
VALUES ('seed','in_store','return','2025-05-01', 100, 100,
        (SELECT id FROM store WHERE code='MTY01'));
ROLLBACK;
\echo '--> expected: ERROR ck_sales_transaction_sign'

\echo ''
\echo '=== CHECK 11: an in-store transaction must name a store'
BEGIN;
INSERT INTO sales_transaction (source_system, channel_code, occurred_at,
                               gross_amount, net_amount)
VALUES ('seed','in_store','2025-05-01', 100, 100);
ROLLBACK;
\echo '--> expected: ERROR channel in_store requires store_id'

\echo ''
\echo '=== CHECK 12: ingestion is idempotent on the source natural key'
INSERT INTO sales_transaction (source_system, external_transaction_id, channel_code,
                               occurred_at, gross_amount, net_amount, store_id)
VALUES ('seed','INV-1001','in_store','2025-05-01', 250, 250,
        (SELECT id FROM store WHERE code='MTY01'));
BEGIN;
INSERT INTO sales_transaction (source_system, external_transaction_id, channel_code,
                               occurred_at, gross_amount, net_amount, store_id)
VALUES ('seed','INV-1001','in_store','2025-05-01', 250, 250,
        (SELECT id FROM store WHERE code='MTY01'));
ROLLBACK;
\echo '--> expected: ERROR duplicate key ux_sales_transaction_external'
\echo '    Without this, "make seed twice leaves row counts unchanged" is unachievable.'

\echo ''
\echo '=== CHECK 13: overlapping consent for one purpose is REJECTED'
INSERT INTO privacy_notice_version (version, published_at, document_uri, checksum_sha256, is_current)
VALUES ('2026.1','2026-01-01','gs://bucket/notice-2026.1.pdf', repeat('a',64), true);
INSERT INTO consent_record (customer_id, purpose_code, notice_version_id, decision,
                            source_channel, valid_from)
SELECT (SELECT id FROM customer WHERE external_customer_id='C0001'),
       'behavioral_profiling', id, 'granted', 'web_public', '2025-01-01'
FROM privacy_notice_version WHERE version='2026.1';
BEGIN;
INSERT INTO consent_record (customer_id, purpose_code, notice_version_id, decision,
                            source_channel, valid_from)
SELECT (SELECT id FROM customer WHERE external_customer_id='C0001'),
       'behavioral_profiling', id, 'denied', 'mobile', '2025-06-01'
FROM privacy_notice_version WHERE version='2026.1';
ROLLBACK;
\echo '--> expected: ERROR ex_consent_record_no_overlap'

\echo ''
\echo '=== CHECK 14: a store-scoped role assignment requires a store'
BEGIN;
INSERT INTO user_role (user_account_id, role_id)
SELECT (SELECT id FROM user_account LIMIT 1), id FROM role WHERE code='store_manager';
ROLLBACK;
\echo '--> expected: ERROR role_id N is store-scoped and requires scope_store_id'

\echo ''
\echo '=== CHECK 15: a global role must NOT carry a store scope'
BEGIN;
INSERT INTO user_role (user_account_id, role_id, scope_store_id)
SELECT (SELECT id FROM user_account LIMIT 1), id, (SELECT id FROM store LIMIT 1)
FROM role WHERE code='auditor';
ROLLBACK;
\echo '--> expected: ERROR role_id N is global and must not carry scope_store_id'

\echo ''
\echo '=== CHECK 16: line net_amount is computed, not trusted'
INSERT INTO sales_transaction_line
    (sales_transaction_id, line_number, product_id, category_id, quantity, unit_price, discount_amount)
SELECT (SELECT id FROM sales_transaction WHERE external_transaction_id='INV-1001'),
       1, p.id, p.category_id, 3, 22.50, 5.00
FROM   product p WHERE p.sku='SKU-001';
SELECT quantity, unit_price, discount_amount, net_amount,
       CASE WHEN net_amount = 62.50 THEN 'PASS' ELSE 'FAIL' END AS result
FROM   sales_transaction_line;
