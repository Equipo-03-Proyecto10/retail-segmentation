-- =========================================================
-- 02_seed_30_per_table.sql
-- MOSAIQ — demonstration data: at least 30 rows per table.
--
-- Runs against the schema created by 01_schema.sql.
--
-- Notes on how this seed is built:
--
-- 1) UUIDs are deterministic (00000000-…-000000000001 and so on) so that
--    dependent tables can reference customers and users inside this same
--    script, and so that a reload produces the same database twice.
--
-- 2) role, channel and segment_label hold fewer than 30 rows. Their domains
--    are seven roles, five sales channels and six ordered business labels;
--    padding them with filler to reach the minimum would make the seed
--    misrepresent the business. Those three, and only those, are listed in
--    sql/seed-exempt.txt with the reason, which is the mechanism CI reads.
--    Every other table holds at least 30 rows, including the runtime tables
--    app_session, sales_load and sales_load_rejection: their rows record the
--    demonstration history the rest of the seed implies (sign-ins that ended,
--    the CSV uploads that brought the sales in), none of them still open.
--
-- 3) audit_log is not inserted into by hand. The triggers in 01_schema.sql
--    fill it as this script loads the audited tables, which is also what
--    demonstrates the audit trail without a single manual row.
--
-- 4) Everything dated is dated relative to the moment the script runs (now(),
--    CURRENT_DATE), so the sales, the segmentation runs, the experiments and the
--    sessions always agree about when things happened, whenever the seed is
--    loaded. Nothing uses random(): variety comes from generate_series and
--    modular arithmetic, so a reload gives the same relative history. Campaign
--    dates are the exception and stay absolute (see the campaign block).
--
-- 5) The analytics are exercised end to end (#463): customers buy a different
--    number of times, so every quintile and every segment band is reached;
--    thirty rule-based runs and one K-means run are recorded as the application
--    records them; and every experiment is assigned, exposed and measured
--    inside its own dates and conversion window.
-- =========================================================

BEGIN;

-- ---------- role (7) ----------
-- The permission matrix in docs/data-model.md. ADMIN is first because the
-- single-administrator rule is about that role specifically.
INSERT INTO role (role_id, code, description) VALUES
(1,'ADMIN','System administrator'),
(2,'ANALYST','Commercial analyst'),
(3,'STORE_MANAGER','Store manager'),
(4,'MARKETING','Marketing'),
(5,'INVENTORY_PLANNER','Inventory planner'),
(6,'AUDITOR','Auditor'),
(7,'CUSTOMER','Loyalty programme customer');

-- ---------- channel (5) ----------
INSERT INTO channel (channel_id, name) VALUES
(1,'mobile_app'),(2,'web'),(3,'physical_store'),(4,'marketplace'),(5,'call_center');

-- ---------- category (10 parents + 20 children = 30) ----------
INSERT INTO category (category_id, name, parent_category_id) VALUES
(1,'Dairy',NULL),(2,'Bakery',NULL),(3,'Snacks',NULL),(4,'Beverages',NULL),
(5,'Household cleaning',NULL),(6,'Personal care',NULL),(7,'Frozen',NULL),
(8,'Fruit and vegetables',NULL),(9,'Meat and fish',NULL),(10,'Small electronics',NULL);

INSERT INTO category (category_id, name, parent_category_id) VALUES
(11,'Milk',1),(12,'Yoghurt',1),
(13,'Sweet bread',2),(14,'Savoury bread',2),
(15,'Crisps',3),(16,'Biscuits',3),
(17,'Soft drinks',4),(18,'Juices',4),
(19,'Detergents',5),(20,'Disinfectants',5),
(21,'Shampoo',6),(22,'Soap',6),
(23,'Ice cream',7),(24,'Frozen vegetables',7),
(25,'Seasonal fruit',8),(26,'Fresh vegetables',8),
(27,'Chicken',9),(28,'Beef',9),
(29,'Headphones',10),(30,'Chargers',10);

-- ---------- store (30) ----------
INSERT INTO store (store_id, name, city, state, is_active)
SELECT n, 'Store ' || n,
       (ARRAY['Monterrey','Guadalupe','San Nicolás','Apodaca','Guadalajara','CDMX','Puebla','Querétaro','Tijuana','Mérida'])[1+((n-1)%10)],
       (ARRAY['Nuevo León','Nuevo León','Nuevo León','Nuevo León','Jalisco','CDMX','Puebla','Querétaro','Baja California','Yucatán'])[1+((n-1)%10)],
       TRUE
FROM generate_series(1,30) n;

-- ---------- segment_rule (30 RFM bands that partition the 125 triples) ----------
-- Every (R, F, M) triple of scores 1 to 5 falls in exactly one band, so the
-- fallback in web/db/segments.py is a last resort that a seeded run never needs
-- (#345, ADR-0018). Segment n takes the label of position (n-1) % 6, so each
-- label owns five bands; the comment on each row names it. Recency and
-- frequency place a customer on a grid and monetary splits the bottom of it:
--   CHAMPION     recent and frequent (R and F 4-5); the best cell splits on M
--   LOYAL        frequent or recent-enough regulars (F 3-5 with R 3, or F 3)
--   POTENTIAL    recent but infrequent (F 1-2 with R 3-5)
--   AT_RISK      used to buy often, recency has slipped (R 1-2, F 3-5)
--   HIBERNATING  low recency and frequency but a mid or high spend (M 3-5)
--   LOST         low recency, frequency and spend (M 1-2)
-- tests/test_seed_segment_rules.py checks the partition arithmetic.
INSERT INTO segment_rule (rule_id, rule_code, r_min, r_max, f_min, f_max, m_min, m_max) VALUES
    (1, 'RULE_001', 5, 5, 5, 5, 4, 5),  -- CHAMPION
    (2, 'RULE_002', 5, 5, 3, 3, 1, 5),  -- LOYAL
    (3, 'RULE_003', 5, 5, 2, 2, 1, 5),  -- POTENTIAL
    (4, 'RULE_004', 2, 2, 5, 5, 1, 5),  -- AT_RISK
    (5, 'RULE_005', 1, 1, 3, 3, 3, 5),  -- HIBERNATING
    (6, 'RULE_006', 1, 1, 3, 3, 1, 2),  -- LOST
    (7, 'RULE_007', 5, 5, 5, 5, 1, 3),  -- CHAMPION
    (8, 'RULE_008', 4, 4, 3, 3, 1, 5),  -- LOYAL
    (9, 'RULE_009', 4, 4, 2, 2, 1, 5),  -- POTENTIAL
    (10, 'RULE_010', 2, 2, 4, 4, 1, 5),  -- AT_RISK
    (11, 'RULE_011', 2, 2, 2, 2, 3, 5),  -- HIBERNATING
    (12, 'RULE_012', 2, 2, 2, 2, 1, 2),  -- LOST
    (13, 'RULE_013', 5, 5, 4, 4, 1, 5),  -- CHAMPION
    (14, 'RULE_014', 3, 3, 5, 5, 1, 5),  -- LOYAL
    (15, 'RULE_015', 3, 3, 2, 2, 1, 5),  -- POTENTIAL
    (16, 'RULE_016', 2, 2, 3, 3, 1, 5),  -- AT_RISK
    (17, 'RULE_017', 1, 1, 2, 2, 3, 5),  -- HIBERNATING
    (18, 'RULE_018', 1, 1, 2, 2, 1, 2),  -- LOST
    (19, 'RULE_019', 4, 4, 5, 5, 1, 5),  -- CHAMPION
    (20, 'RULE_020', 3, 3, 4, 4, 1, 5),  -- LOYAL
    (21, 'RULE_021', 3, 3, 1, 1, 1, 5),  -- POTENTIAL
    (22, 'RULE_022', 1, 1, 5, 5, 1, 5),  -- AT_RISK
    (23, 'RULE_023', 2, 2, 1, 1, 3, 5),  -- HIBERNATING
    (24, 'RULE_024', 2, 2, 1, 1, 1, 2),  -- LOST
    (25, 'RULE_025', 4, 4, 4, 4, 1, 5),  -- CHAMPION
    (26, 'RULE_026', 3, 3, 3, 3, 1, 5),  -- LOYAL
    (27, 'RULE_027', 4, 5, 1, 1, 1, 5),  -- POTENTIAL
    (28, 'RULE_028', 1, 1, 4, 4, 1, 5),  -- AT_RISK
    (29, 'RULE_029', 1, 1, 1, 1, 3, 5),  -- HIBERNATING
    (30, 'RULE_030', 1, 1, 1, 1, 1, 2);  -- LOST

-- ---------- segment_label (6, best to worst — ADR-0018) ----------
-- Inserted before segment: segment.label_code references this vocabulary.
INSERT INTO segment_label (label_code, ordinal_position, name, description) VALUES
    ('CHAMPION',   1, 'Champion',    'Highest recency, frequency and monetary value'),
    ('LOYAL',      2, 'Loyal',       'Buys often and recently, consistent spend'),
    ('POTENTIAL',  3, 'Potential',   'Recent customer with room to grow frequency'),
    ('AT_RISK',    4, 'At risk',     'Used to buy often, recency has slipped'),
    ('HIBERNATING',5, 'Hibernating', 'Low recency, frequency and monetary value'),
    ('LOST',       6, 'Lost',        'No recent activity across all three measures');

-- ---------- segment (30, one per rule, 5 per label, label = position (n-1) % 6) ----------
INSERT INTO segment (segment_id, name, description, rule_id, label_code, valid_from, valid_to)
SELECT n, 'Segment ' || n, 'Segment derived from RULE_' || lpad(n::text,3,'0'), n,
       (ARRAY['CHAMPION','LOYAL','POTENTIAL','AT_RISK','HIBERNATING','LOST'])[1+((n-1)%6)],
       DATE '2026-01-01', NULL
FROM generate_series(1,30) n;


-- ---------- app_user (30, exactly one administrator) ----------
-- AGENTS.md: there is exactly one administrator. User 1 holds role 1 and no
-- other user does, so the partial unique index F4-02 adds applies to this data
-- without anyone having to rewrite the seed first.
--
-- Every demonstration account shares the password Password123!, hashed with
-- argon2id — the same algorithm web/requirements.txt pins, so F3-03 can verify
-- these users as they stand. The hash is identical across rows because the
-- password is; real accounts created through the application get their own
-- salt per user.
INSERT INTO app_user (user_id, role_id, name, email, password_hash, is_active, created_at)
SELECT ('11111111-1111-1111-1111-' || lpad(n::text,12,'0'))::uuid,
       CASE WHEN n = 1 THEN 1 ELSE 2 + ((n - 2) % 6) END,
       CASE WHEN n = 1 THEN 'MOSAIQ Administrator' ELSE 'Demo User ' || n END,
       CASE WHEN n = 1 THEN 'admin@mosaiq-demo.com' ELSE 'user' || n || '@mosaiq-demo.com' END,
       '$argon2id$v=19$m=65536,t=3,p=4$BTg0ZDnIiX0UXEv10Pa1Pw$kPMnEtE7i3rQKZhkDCbDiN3R6FEKkEmsN2CKncGfd1c',
       TRUE,
       now() - (n || ' days')::interval
FROM generate_series(1,30) n;

-- ---------- app_session (30, one finished sign-in per user) ----------
-- Server-side sessions (ADR-0022): the history of each demonstration account's
-- last sign-in. Every session has ended (revoked_at is set), so none can
-- authenticate a request: load_principal() requires revoked_at IS NULL. The
-- session ids are fixed so a reload gives the same rows. User n signed in n
-- days before the load, a few hours after the account was created, stayed
-- 25 to 100 minutes and signed out one minute after the last request.
INSERT INTO app_session (session_id, user_id, created_at, last_seen_at, revoked_at)
SELECT ('22222222-2222-2222-2222-' || lpad(n::text,12,'0'))::uuid,
       ('11111111-1111-1111-1111-' || lpad(n::text,12,'0'))::uuid,
       s.created_at,
       s.created_at + make_interval(mins => 25 + (n * 7) % 76),
       s.created_at + make_interval(mins => 26 + (n * 7) % 76)
FROM generate_series(1,30) n
CROSS JOIN LATERAL (
    SELECT now() - make_interval(days => n) + make_interval(hours => 2 + n % 6) AS created_at
) AS s;

-- ---------- customer (30) ----------
-- user_id stays NULL: a customer record and an application account are
-- separate things. A future customer self-service story must define how a
-- loyalty account claims and maintains this optional ownership link before it
-- populates it; the current administrator user workflow does not guess.
INSERT INTO customer (customer_id, user_id, name, email, phone, registration_channel_id, registered_on)
SELECT ('00000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
       NULL,
       'Demo Customer ' || n,
       'customer' || n || '@mosaiq-demo.com',
       '55' || lpad(n::text,8,'0'),
       1 + ((n-1) % 5),
       -- Every seeded sale is at most 180 days old.  Register the customer
       -- before that history; otherwise a fresh seed could show purchases
       -- months before the customer's registration date.
       CURRENT_DATE - (180 + n)
FROM generate_series(1,30) n;

-- ---------- segmentation runs (F7-02) ----------
INSERT INTO segmentation_run (method, window_days, parameters, customer_count, run_at)
SELECT 'RFM_RULES', 180, '{"window_days": 180}'::jsonb, 30,
       now() - (n || ' days')::interval
FROM generate_series(29, 1, -1) n;

-- One completed K-means run (F9-02 to F9-05, ADR-0018, ADR-0021, ADR-0030),
-- twelve hours before the last RFM_RULES run, so the model comparison, the
-- cluster labels and the run history have a second method to show, while the
-- newest run, and so every customer's current assignment, stays rule-based.
--
-- Its parameters are shaped exactly as web/services/kmeans.py records them
-- (recorded_parameters plus the label mapping and unassigned count that
-- web/services/segmentation.py adds): k, seed, iteration limit, tolerance,
-- window, the stated policies, the label mapping and the quality measures.
-- They are not invented. The fit is the application's own, k = 4 and seed 463
-- over the raw recency, frequency and monetary values that this run's history
-- rows carry (computed below from the seeded sales), and the label of each
-- customer in the kmeans_label list further down is what that fit assigned,
-- paired with the vocabulary by web/services/cluster_labels.py. A K-means row
-- has a label and its raw values but no segment and no quintile scores
-- (ADR-0018). tests/test_seed_analytics_postgres.py refits the seeded run and
-- fails if this record and the seed's sales ever disagree.
INSERT INTO segmentation_run (method, window_days, parameters, customer_count, run_at)
VALUES ('KMEANS', 180, '{
  "empty_cluster_policy": "an emptied cluster takes the customer farthest from their own cluster''s centre, from a cluster holding at least two; lowest customer id on a tie",
  "initialisation": "k-means++, seeded",
  "k": 4,
  "label_mapping": {
    "clusters_per_label": {
      "AT_RISK": 1,
      "CHAMPION": 1,
      "HIBERNATING": 0,
      "LOST": 1,
      "LOYAL": 0,
      "POTENTIAL": 1
    },
    "labels_by_rank": [
      "CHAMPION",
      "POTENTIAL",
      "AT_RISK",
      "LOST"
    ],
    "labels_without_cluster": [
      "LOYAL",
      "HIBERNATING"
    ],
    "rule": "rank_proportional_endpoints_anchored",
    "shared_labels": [],
    "vocabulary_size": 6
  },
  "max_iterations": 100,
  "normalisation": "min-max to [0, 1] across the run; recency reversed so higher is better; a constant feature is 0",
  "quality": {
    "cluster_sizes": [
      12,
      7,
      7,
      4
    ],
    "converged": true,
    "customers_clustered": 30,
    "customers_unassigned": 0,
    "empty_cluster_events": 0,
    "final_shift": 0.0,
    "inertia": 2.813246,
    "iterations": 3,
    "silhouette": 0.236376,
    "stopped_on": "tolerance"
  },
  "seed": 463,
  "tie_break": "a customer equidistant from two clusters goes to the lower-numbered one",
  "tolerance": 0.0001,
  "window_days": 180
}'::jsonb, 30, now() - interval '12 hours');

INSERT INTO segmentation_run (method, window_days, parameters, customer_count, run_at)
VALUES ('RFM_RULES', 180, '{"window_days": 180}'::jsonb, 30, now());

-- ---------- customer_preferred_channel (30 customers x 2 channels = 60) ----------
INSERT INTO customer_preferred_channel (customer_id, channel_id)
SELECT ('00000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
       1 + ((n + c) % 5)
FROM generate_series(1,30) n
CROSS JOIN generate_series(0,1) c;

-- ---------- customer_interest_category (30 customers x 2 categories = 60) ----------
INSERT INTO customer_interest_category (customer_id, category_id)
SELECT ('00000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
       1 + ((n*2 + c) % 30)
FROM generate_series(1,30) n
CROSS JOIN generate_series(0,1) c;

-- ---------- product (40) ----------
-- image_path stays NULL: F3-07 writes it when a file is actually uploaded, and
-- a seeded path pointing at a file that does not exist would make the upload
-- feature look broken on a fresh clone.
INSERT INTO product (product_id, sku, name, category_id, list_price, image_path, is_active)
SELECT n, 'SKU-' || lpad(n::text,5,'0'), 'Demo Product ' || n,
       1 + ((n-1) % 30),
       round((15 + (n % 50) * 3.75)::numeric,2),
       NULL,
       TRUE
FROM generate_series(1,40) n;

-- ---------- transaction (300, varied by customer, within the last 180 days) ----------
-- Customers do not behave alike. Each one has a purchase count, a day of last
-- purchase and a spacing between purchases, all derived from the customer
-- number by modular arithmetic (no random(), so a reload gives the same
-- relative history):
--
--   purchases  3 + p/2, where p = (7c mod 30) runs over 0..29, so counts run
--              3..17 in pairs and add up to exactly 300
--   last_days  4 * ((23p + 4) mod 30), 0..116 days before the load, a second
--              permutation that leaves recency only loosely tied to frequency
--   spacing    the days between purchases: the rest of the 180-day window
--              shared out, at most 45, so a frequent customer buys every few
--              days and an occasional one every month or two. Customer 30 is
--              new: three purchases three days apart, all in the last three
--              weeks, so the older runs find no sale in its window and record
--              it as unassigned (RN-21)
--
-- That spreads recency, frequency and spend across the whole range, which is
-- what lets every quintile and every segment band be reached (#463): a frequent
-- customer whose last purchase is old is AT_RISK, an infrequent recent one is
-- POTENTIAL, and so on. With every customer at exactly 10 purchases the
-- frequency quintile came only from the customer-id tie-break.
--
-- source_transaction_id mirrors the sales contract's identifier (F8-01): a
-- deterministic, unique string per row, in the same spirit as product.sku, and
-- numbered customer by customer so TXN-00000001 is customer 1's latest sale.
-- Every sale is at most 180 days old. Offsets are counted in hours, 24 a day, so
-- they are elapsed time under any TimeZone: the raw recency, frequency and spend
-- of a customer, and so the K-means run recorded below, do not depend on where
-- the script is run.
INSERT INTO transaction (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
WITH profile AS (
    SELECT c,
           3 + ((c*7) % 30) / 2 AS purchases,
           4 * ((23 * ((c*7) % 30) + 4) % 30) AS last_days
    FROM generate_series(1,30) c
),
sale AS (
    SELECT p.c, j, p.last_days,
           CASE WHEN p.c = 30 THEN 3
                ELSE LEAST(45, (179 - p.last_days) / p.purchases)
           END AS spacing_days,
           row_number() OVER (ORDER BY p.c, j) AS n
    FROM profile AS p
    CROSS JOIN LATERAL generate_series(1, p.purchases) j
)
SELECT 'TXN-' || lpad(n::text,8,'0'),
       ('00000000-0000-0000-0000-' || lpad(c::text,12,'0'))::uuid,
       1 + ((n*7) % 30),
       1 + ((n*3) % 5),
       now() - make_interval(hours => 24 * (last_days + (j-1) * spacing_days)
                                       + (c*5 + j*7) % 24),
       round((50 + (n % 20) * 37.5)::numeric,2)
FROM sale
ORDER BY n;

-- ---------- transaction_line (2 lines per transaction = 600) ----------
-- A customer's unit prices are scaled by a customer factor of 1.0 to 3.0 (the
-- customer number is the last twelve digits of the seeded customer id), so two
-- customers with the same purchase count do not spend the same.
INSERT INTO transaction_line (transaction_id, product_id, quantity, unit_price)
SELECT t.transaction_id,
       1 + ((t.transaction_id + d) % 40),
       1 + ((t.transaction_id + d) % 3),
       round(((10 + ((t.transaction_id + d) % 15) * 3.5)
              * (1 + ((right(t.customer_id::text, 12)::int * 13) % 5) * 0.5))::numeric, 2)
FROM transaction t
CROSS JOIN generate_series(0,1) d;

-- ADR-0020: a header total is derived from its persisted lines, never supplied
-- beside them. The insert above writes a placeholder; this makes every seeded
-- total reconcile with its own lines (#254).
UPDATE transaction AS t
SET total = l.line_total
FROM (
    SELECT transaction_id, SUM(quantity * unit_price) AS line_total
    FROM transaction_line
    GROUP BY transaction_id
) AS l
WHERE l.transaction_id = t.transaction_id;

-- ---------- customer_segment_history (F7-02) ----------
-- History is a materialized record of each run's RFM input, not a second
-- illustrative dataset.  Derive every raw value from the transactions that
-- existed at that run's timestamp, and derive scores and labels with the same
-- quintile/rule procedure as web/db/segments.py.  Keeping this after the
-- transaction lines (and their reconciled headers) means the current profile,
-- run history and sales block all describe the same accepted sales.
--
-- The K-means run is recorded the way the application records one: the same
-- raw values from its own window, the label its fit gave the customer, and NULL
-- for the segment and the three scores. A customer with no sale in a run's
-- window gets the unassigned row (no label, no values) in both methods.
INSERT INTO customer_segment_history
    (customer_id, run_id, segment_id, label_code,
     recency_last_purchase_at, frequency_count, monetary_total,
     r_score, f_score, m_score, valid_from, valid_to)
WITH ordered_runs AS (
    SELECT run_id,
           method,
           run_at,
           window_days,
           lead(run_at) OVER (ORDER BY run_at, run_id) AS next_run_at
    FROM segmentation_run
),
window_sales AS (
    SELECT r.run_id,
           r.run_at,
           r.next_run_at,
           t.customer_id,
           max(t.occurred_at) AS last_purchase,
           count(*) AS frequency,
           sum(t.total) AS monetary
    FROM ordered_runs AS r
    JOIN transaction AS t
      ON t.occurred_at >= r.run_at - make_interval(days => r.window_days)
     AND t.occurred_at <= r.run_at
    GROUP BY r.run_id, r.run_at, r.next_run_at, t.customer_id
),
scored AS (
    SELECT ws.*,
           6 - ntile(5) OVER (
                   PARTITION BY ws.run_id
                   ORDER BY ws.last_purchase DESC, ws.customer_id
               ) AS r_score,
           6 - ntile(5) OVER (
                   PARTITION BY ws.run_id
                   ORDER BY ws.frequency DESC, ws.customer_id
               ) AS f_score,
           6 - ntile(5) OVER (
                   PARTITION BY ws.run_id
                   ORDER BY ws.monetary DESC, ws.customer_id
               ) AS m_score
    FROM window_sales AS ws
),
-- What the K-means fit assigned each customer, by customer number, 1 to 30.
kmeans_label AS (
    SELECT ('00000000-0000-0000-0000-' || lpad(k.n::text,12,'0'))::uuid AS customer_id,
           k.label_code
    FROM unnest(ARRAY[
        'LOST', 'AT_RISK', 'CHAMPION', 'POTENTIAL', 'LOST',
        'CHAMPION', 'AT_RISK', 'CHAMPION', 'LOST', 'LOST',
        'CHAMPION', 'POTENTIAL', 'LOST', 'LOST', 'AT_RISK',
        'CHAMPION', 'POTENTIAL', 'LOST', 'CHAMPION', 'AT_RISK',
        'POTENTIAL', 'LOST', 'LOST', 'AT_RISK', 'AT_RISK',
        'LOST', 'LOST', 'CHAMPION', 'AT_RISK', 'LOST'
    ]) WITH ORDINALITY AS k(label_code, n)
)
SELECT c.customer_id,
       r.run_id,
       CASE WHEN r.method = 'RFM_RULES'
            THEN COALESCE(matched.segment_id, fallback.segment_id) END,
       CASE WHEN r.method = 'RFM_RULES'
            THEN COALESCE(matched.label_code, fallback.label_code)
            WHEN s.customer_id IS NOT NULL
            THEN km.label_code END,
       s.last_purchase,
       s.frequency::int,
       s.monetary,
       CASE WHEN r.method = 'RFM_RULES' THEN s.r_score END,
       CASE WHEN r.method = 'RFM_RULES' THEN s.f_score END,
       CASE WHEN r.method = 'RFM_RULES' THEN s.m_score END,
       r.run_at,
       r.next_run_at
FROM ordered_runs AS r
CROSS JOIN customer AS c
LEFT JOIN scored AS s
       ON s.run_id = r.run_id AND s.customer_id = c.customer_id
LEFT JOIN kmeans_label AS km ON km.customer_id = c.customer_id
LEFT JOIN LATERAL (
    SELECT seg.segment_id, seg.label_code
    FROM segment AS seg
    JOIN segment_rule AS sr ON sr.rule_id = seg.rule_id
    WHERE r.method = 'RFM_RULES'
      AND s.r_score BETWEEN sr.r_min AND sr.r_max
      AND s.f_score BETWEEN sr.f_min AND sr.f_max
      AND s.m_score BETWEEN sr.m_min AND sr.m_max
      AND seg.valid_from <= CURRENT_DATE
      AND (seg.valid_to IS NULL OR seg.valid_to >= CURRENT_DATE)
    ORDER BY seg.segment_id
    LIMIT 1
) AS matched ON true
LEFT JOIN LATERAL (
    SELECT seg.segment_id, seg.label_code
    FROM segment AS seg
    JOIN segment_label AS sl ON sl.label_code = seg.label_code
    WHERE r.method = 'RFM_RULES'
      AND s.customer_id IS NOT NULL
      AND matched.segment_id IS NULL
      AND seg.valid_from <= CURRENT_DATE
      AND (seg.valid_to IS NULL OR seg.valid_to >= CURRENT_DATE)
    ORDER BY sl.ordinal_position DESC, seg.segment_id
    LIMIT 1
) AS fallback ON true;

-- ---------- campaign (30) ----------
-- Campaign dates are absolute, not relative to the load: a campaign's lifecycle
-- is judged against fixed business dates (RN-49, tests/golden/cases/campaigns.py),
-- so the sales, runs and experiments move with the load and the campaigns do not.
INSERT INTO campaign (campaign_id, name, label_code, starts_on, ends_on, status)
SELECT n, 'Campaign ' || n,
       (ARRAY['CHAMPION','LOYAL','POTENTIAL','AT_RISK','HIBERNATING','LOST'])[1+((n-1)%6)],
       DATE '2026-01-01' + (n || ' days')::interval,
       DATE '2026-01-01' + ((n+30) || ' days')::interval,
       (ARRAY['DRAFT','ACTIVE','FINISHED','CANCELLED'])[1+((n-1)%4)]
FROM generate_series(1,30) n;

-- ---------- experiment (30) ----------
-- Every seeded experiment is synthetic (ADR-0019): none may claim OBSERVED,
-- which would show its figures as real results without the Synthetic label
-- (#427). Two in three are A/A validation, one in three injected uplift.
--
-- An experiment is dated relative to the load, like the sales it is measured
-- against, and runs for 42 days: it starts between 8 and 153 days ago, in steps
-- of five. The seven that started within 42 days are still running, and their
-- later conversion windows are still open; the rest are finished. Assignments
-- fall in an experiment's first ten days and conversion windows are 7 to 28
-- days, so everything an experiment records falls inside its own dates. (The
-- campaign dates are absolute, so an experiment's dates no longer coincide with
-- its campaign's.)
INSERT INTO experiment (experiment_id, name, campaign_id, target_metric, starts_on, ends_on,
                         conversion_window_days, data_origin)
SELECT n, 'Experiment ' || n, n,
       (ARRAY['CONVERSION','AVERAGE_TICKET'])[1+((n-1)%2)],
       CURRENT_DATE - (8 + ((n*11) % 30) * 5),
       CURRENT_DATE - (8 + ((n*11) % 30) * 5) + 42,
       7 + ((n-1) % 4) * 7,
       CASE WHEN n % 3 = 0 THEN 'INJECTED' ELSE 'SEEDED' END
FROM generate_series(1,30) n;

-- ---------- experiment_group (2 per experiment = 60) ----------
INSERT INTO experiment_group (group_id, experiment_id, kind, name, treatment_description)
SELECT (n-1)*2 + 1, n, 'CONTROL', 'Holdout ' || n, 'No treatment delivered.' FROM generate_series(1,30) n
UNION ALL
SELECT (n-1)*2 + 2, n, 'TREATMENT', 'Offer ' || n, 'Personalized offer for experiment ' || n FROM generate_series(1,30) n;

-- ---------- experiment_assignment (4 per experiment = 120) ----------
-- k 0-1 go to the control group, 2-3 to treatment. The customer offset is
-- scoped to the experiment (base (n-1)*4, span 4 < 30) so no customer
-- repeats within one experiment, satisfying UNIQUE (experiment_id, customer_id).
-- Assignee k is assigned 3k days after the experiment starts (the last one on
-- day 9, and never later than a day before the load for an experiment that
-- started recently) plus 0 to 7 hours. The moment is counted back from now(),
-- like every sale, so the gap between an assignment and a customer's sales does
-- not depend on the time of day the seed is loaded; it is always inside the
-- experiment's dates and never in the future.
INSERT INTO experiment_assignment (experiment_id, group_id, customer_id, assigned_at)
SELECT n,
       (n-1)*2 + 1 + (k/2),
       ('00000000-0000-0000-0000-' || lpad(c::text,12,'0'))::uuid,
       now() - make_interval(hours => 24 * GREATEST(CURRENT_DATE - e.starts_on - k*3, 1)
                                       - (n + k*5) % 8)
FROM generate_series(1,30) n
CROSS JOIN generate_series(0,3) k
CROSS JOIN LATERAL (SELECT 1+(((n-1)*4+k) % 30) AS c) AS customer_index
JOIN experiment AS e ON e.experiment_id = n
ORDER BY n, k;

-- ---------- experiment_exposure (one treatment assignee per experiment = 30) ----------
-- Picks the first treatment assignment per experiment by assignment_id,
-- derived structurally from experiment_assignment/experiment_group rather
-- than re-deriving the customer offset formula above, so the two inserts
-- cannot drift apart if that formula changes. The customer is reached two to
-- seven hours after being assigned, so the exposure is after the assignment and
-- inside the experiment's dates; the other treatment assignee stays unexposed
-- (ADR-0019), and a control assignee is never exposed.
INSERT INTO experiment_exposure (assignment_id, exposed_at)
SELECT assignment_id, assigned_at + make_interval(hours => 2 + experiment_id % 6)
FROM (
    SELECT a.assignment_id, a.experiment_id, a.assigned_at,
           ROW_NUMBER() OVER (PARTITION BY a.experiment_id ORDER BY a.assignment_id) AS rn
    FROM experiment_assignment a
    JOIN experiment_group g ON g.group_id = a.group_id
    WHERE g.kind = 'TREATMENT'
) ranked
WHERE rn = 1
ORDER BY assignment_id;

-- ---------- experiment_conversion (every qualifying sale of every assignee) ----------
-- What F11-06 evaluates, as web/db/experiment_conversions.py evaluates it: an
-- assignee's own sale in [assigned_at, assigned_at + conversion_window_days),
-- in either arm (the uplift is measured from every assigned customer). A
-- customer with no such sale has no row, and one whose window is still open may
-- convert later. converted_at is the moment of the sale, so a conversion is
-- inside the window it was judged against and inside the experiment's dates.
INSERT INTO experiment_conversion (assignment_id, transaction_id, converted_at)
SELECT a.assignment_id, t.transaction_id, t.occurred_at
FROM experiment_assignment a
JOIN experiment x ON x.experiment_id = a.experiment_id
JOIN transaction t
  ON t.customer_id = a.customer_id
 AND t.occurred_at >= a.assigned_at
 AND t.occurred_at < a.assigned_at + make_interval(days => x.conversion_window_days)
ORDER BY a.assignment_id, t.occurred_at, t.transaction_id;

-- ---------- inventory (30 stores x 5 products = 150) ----------
INSERT INTO inventory (store_id, product_id, quantity_on_hand)
SELECT s, p, 10 + ((s+p) % 90)
FROM generate_series(1,30) s
CROSS JOIN generate_series(1,5) p;

-- ---------- sales_load (30 uploads of 10 accepted sales each) ----------
-- Sales CSV uploads by the administrator (F8-01, #334): one a day for the last
-- thirty days, version 1 of the contract. Each accepted ten sales, 300 in all,
-- the number of seeded sales (the history was backfilled by file, whatever the
-- dates of the sales themselves), and refused 0 to 4 lines. A load is
-- reconciled: received = accepted + rejected.
INSERT INTO sales_load (filename, contract_version, received_count, accepted_count,
                        rejected_count, loaded_by, loaded_at)
SELECT 'sales-' || to_char((now() - make_interval(days => 31 - n))::date, 'YYYY-MM-DD') || '.csv',
       1,
       10 + n % 5,
       10,
       n % 5,
       '11111111-1111-1111-1111-000000000001',
       now() - make_interval(days => 31 - n) + interval '11 hours'
FROM generate_series(1,30) n
ORDER BY n;

-- ---------- sales_load_rejection (one row per refused line = 60) ----------
-- The refused lines of those loads, numbered as the file numbers them (the
-- header is line 1, so the first data row is line 2), with the reasons the
-- ingestion rules in web/services/ingestion.py give.
INSERT INTO sales_load_rejection (load_id, line_number, reason)
SELECT l.load_id,
       2 + k * 2,
       (ARRAY['unknown customer',
              'unknown product',
              'product is inactive',
              'quantity must be positive',
              'unit price must have at most 2 decimal places',
              'occurred_at cannot be in the future',
              'sale occurred before customer registration'])[1 + (l.load_id + k) % 7]
FROM sales_load AS l
CROSS JOIN LATERAL generate_series(0, l.rejected_count - 1) AS k
ORDER BY l.load_id, k;

-- ---------- audit_log ----------
-- Filled by the triggers as the statements above ran: segment, segment_rule,
-- campaign, experiment, category, product, store, channel, role, customer,
-- app_user and customer_segment_history. Nothing is inserted here by hand.

COMMIT;
