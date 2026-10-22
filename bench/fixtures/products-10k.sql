-- 10,000 deterministic products for the products reads benchmark (ADR-0042).
-- Data only, bench database only: bench/compose.yaml runs it after the three
-- scripts. Ids start above the seed's, every row is the same on every run, and
-- the rows spread over the seeded categories, prices and a few inactive ones,
-- so searches, filters and pages have work to do.
BEGIN;

INSERT INTO product (product_id, sku, name, category_id, list_price, image_path, is_active)
SELECT 100000 + n,
       'BENCH-' || lpad(n::text, 6, '0'),
       (ARRAY['Organic', 'Classic', 'Family', 'Light', 'Premium'])[1 + n % 5] || ' '
         || (ARRAY['milk', 'bread', 'crisps', 'juice', 'detergent', 'soap', 'peas',
                   'apples', 'salmon', 'charger'])[1 + n % 10] || ' ' || n,
       1 + (n - 1) % 30,
       round((5 + (n % 400) * 1.25)::numeric, 2),
       NULL,
       n % 17 <> 0
FROM generate_series(1, 10000) AS n;

COMMIT;
