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
-- 2) role and channel hold fewer than 30 rows. Their domains are seven roles
--    and five sales channels; padding them with filler to reach the minimum
--    would make the seed misrepresent the business. Both are listed in
--    sql/seed-exempt.txt with the reason, which is the mechanism CI reads.
--
-- 3) audit_log is not inserted into by hand. The triggers in 01_schema.sql
--    fill it as this script loads the audited tables, which is also what
--    demonstrates the audit trail without a single manual row.
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

-- ---------- segment_rule (30 RFM band combinations) ----------
INSERT INTO segment_rule (rule_id, rule_code, r_min, r_max, f_min, f_max, m_min, m_max)
SELECT n,
       'RULE_' || lpad(n::text,3,'0'),
       1 + ((n-1) % 3), 3 + ((n-1) % 3),
       1 + ((n*2-1) % 3), 3 + ((n*2-1) % 3),
       1 + ((n*3-1) % 3), 3 + ((n*3-1) % 3)
FROM generate_series(1,30) n;

-- ---------- segment (30, one per rule) ----------
INSERT INTO segment (segment_id, name, description, rule_id, valid_from, valid_to)
SELECT n, 'Segment ' || n, 'Segment derived from RULE_' || lpad(n::text,3,'0'), n, DATE '2026-01-01', NULL
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

-- ---------- customer (30) ----------
-- user_id stays NULL: a customer record and an application account are
-- separate things, and linking them is what F3-06 does for the loyalty
-- customers who actually sign in.
INSERT INTO customer (customer_id, user_id, name, email, phone, registration_channel_id, current_segment_id, registered_on)
SELECT ('00000000-0000-0000-0000-' || lpad(n::text,12,'0'))::uuid,
       NULL,
       'Demo Customer ' || n,
       'customer' || n || '@mosaiq-demo.com',
       '55' || lpad(n::text,8,'0'),
       1 + ((n-1) % 5),
       1 + ((n-1) % 30),
       CURRENT_DATE - (n*7 || ' days')::interval
FROM generate_series(1,30) n;

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

-- ---------- transaction (300, spread over roughly six months) ----------
-- Six months of history is what makes the deferred RFM work meaningful later:
-- recency and frequency need a window to be measured over.
INSERT INTO transaction (customer_id, store_id, channel_id, occurred_at, total)
SELECT ('00000000-0000-0000-0000-' || lpad((1+((n-1)%30))::text,12,'0'))::uuid,
       1 + ((n*7) % 30),
       1 + ((n*3) % 5),
       now() - ((n % 180) || ' days')::interval,
       round((50 + (n % 20) * 37.5)::numeric,2)
FROM generate_series(1,300) n;

-- ---------- transaction_line (2 lines per transaction = 600) ----------
INSERT INTO transaction_line (transaction_id, product_id, quantity, unit_price)
SELECT t.transaction_id,
       1 + ((t.transaction_id + d) % 40),
       1 + ((t.transaction_id + d) % 3),
       round((20 + ((t.transaction_id + d) % 15) * 5.25)::numeric,2)
FROM transaction t
CROSS JOIN generate_series(0,1) d;

-- ---------- campaign (30) ----------
INSERT INTO campaign (campaign_id, name, segment_id, starts_on, ends_on, status)
SELECT n, 'Campaign ' || n, n,
       DATE '2026-01-01' + (n || ' days')::interval,
       DATE '2026-01-01' + ((n+30) || ' days')::interval,
       (ARRAY['DRAFT','ACTIVE','FINISHED','CANCELLED'])[1+((n-1)%4)]
FROM generate_series(1,30) n;

-- ---------- experiment (30) ----------
INSERT INTO experiment (experiment_id, name, campaign_id, target_metric, starts_on, ends_on)
SELECT n, 'Experiment ' || n, n,
       (ARRAY['CONVERSION','AVERAGE_TICKET'])[1+((n-1)%2)],
       DATE '2026-02-01', DATE '2026-03-01'
FROM generate_series(1,30) n;

-- ---------- experiment_group (2 per experiment = 60) ----------
INSERT INTO experiment_group (group_id, experiment_id, kind)
SELECT (n-1)*2 + 1, n, 'CONTROL' FROM generate_series(1,30) n
UNION ALL
SELECT (n-1)*2 + 2, n, 'TREATMENT' FROM generate_series(1,30) n;

-- ---------- experiment_group_customer (60 groups x 2 customers = 120) ----------
INSERT INTO experiment_group_customer (group_id, customer_id)
SELECT g, ('00000000-0000-0000-0000-' || lpad((1+((g*7+c) % 30))::text,12,'0'))::uuid
FROM generate_series(1,60) g
CROSS JOIN generate_series(0,1) c;

-- ---------- inventory (30 stores x 5 products = 150) ----------
INSERT INTO inventory (store_id, product_id, quantity_on_hand)
SELECT s, p, 10 + ((s+p) % 90)
FROM generate_series(1,30) s
CROSS JOIN generate_series(1,5) p;

-- ---------- audit_log ----------
-- Filled by the triggers as the statements above ran: segment, segment_rule,
-- campaign, experiment, category, product, store, customer and app_user.
-- Nothing is inserted here by hand.

COMMIT;
