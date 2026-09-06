# F2-07 — Integrity verification

Evidence that the constraints in [`sql/01_schema.sql`](../../sql/01_schema.sql)
actually refuse bad data, rather than being asserted to.

Reproduced by [`sql/verify_integrity.sql`](../../sql/verify_integrity.sql),
which runs every case below in its own transaction and rolls back, so it leaves
no trace and can be re-run at will.

## How this run was produced

```bash
docker run -d --name mosaiq-check -e POSTGRES_PASSWORD=postgres -p 55432:5432 postgres:16

export PGHOST=localhost PGPORT=55432 PGUSER=postgres PGPASSWORD=postgres
psql -v ON_ERROR_STOP=1 -f sql/00_create_database.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/01_schema.sql
psql -v ON_ERROR_STOP=1 -d retail -f sql/02_seed_30_per_table.sql
psql -d retail -f sql/verify_integrity.sql
```

Environment: PostgreSQL 16.15 on Debian, in a throwaway container. The three
ordered scripts each exited 0.

## Result

| | Cases | Outcome |
|---|---|---|
| Positive | 4 | All succeeded |
| Negative | 16 | All refused by the database |

Every table carries at least 30 rows except `role` (7) and `channel` (5), both
listed in [`sql/seed-exempt.txt`](../../sql/seed-exempt.txt) with their reason.

## Cases

| # | Case | Constraint that refused it |
|---|---|---|
| N1 | Duplicate primary key in `role` | `role_pkey` |
| N2 | Duplicate `product.sku` | `product_sku_key` |
| N3 | Duplicate `app_user.email` | `app_user_email_key` |
| N4 | `product` referencing a category that does not exist | `product_category_id_fkey` |
| N5 | `transaction` referencing a customer that does not exist | `transaction_customer_id_fkey` |
| N6 | Negative `product.list_price` | `product_list_price_check` |
| N7 | `app_user.email` without `@` | `app_user_email_check` |
| N8 | `campaign.status` outside its domain | `campaign_status_check` |
| N9 | RFM band outside 1–5 | `segment_rule_r_max_check` |
| N10 | `transaction_line.quantity` of zero | `transaction_line_quantity_check` |
| N11 | Campaign ending before it starts | `campaign_check` |
| N12 | `product.name` null | not-null on `name` |
| N13 | Deleting a category that has products | `product_category_id_fkey` (`RESTRICT`) |
| N14 | Deleting a customer that has sales | `transaction_customer_id_fkey` (`RESTRICT`) |
| N15 | Duplicate composite key in `inventory` | `inventory_pkey` |
| N16 | Deleting a parent category that has children | `category_parent_category_id_fkey` (`RESTRICT`) |

| # | Case | What it proves |
|---|---|---|
| P1 | A well-formed product inserts | The constraints refuse bad data without refusing good data |
| P2 | Updating a catalog row writes an audit entry | The audit trigger fires on the catalogs the administrator module edits |
| P3 | The audit payload carries no password hash | `fn_audit()` strips `password_hash` before writing |
| P4 | Deleting a transaction removes its lines | `ON DELETE CASCADE` on `transaction_line` |

P2 and P3 are what the demonstration's "registro de auditoría" item rests on.

## Full transcript

```text
Timing is off.
==============================================
POSITIVE CASES — these must succeed
==============================================

-- P1: a well-formed product inserts
BEGIN
INSERT 0 1
    ?column?    
----------------
 P1 inserted: 1
(1 row)

ROLLBACK

-- P2: updating a catalog row writes an audit entry
BEGIN
 audit_rows_before 
-------------------
                40
(1 row)

UPDATE 1
 audit_rows_after 
------------------
               41
(1 row)

          ?column?          
----------------------------
 P2 action recorded: UPDATE
(1 row)

ROLLBACK

-- P3: the audit payload never carries a password hash
BEGIN
UPDATE 1
             ?column?              
-----------------------------------
 P3 hash present in payload: false
(1 row)

ROLLBACK

-- P4: deleting a transaction cascades to its lines
BEGIN
      ?column?      
--------------------
 P4 lines before: 2
(1 row)

DELETE 1
     ?column?      
-------------------
 P4 lines after: 0
(1 row)

ROLLBACK

==============================================
NEGATIVE CASES — every one of these must be refused
==============================================

-- N1: duplicate primary key                    [expect: 23505 unique_violation]
BEGIN
psql:sql/verify_integrity.sql:67: ERROR:  duplicate key value violates unique constraint "role_pkey"
DETAIL:  Key (role_id)=(1) already exists.
ROLLBACK

-- N2: duplicate unique sku                     [expect: 23505 unique_violation]
BEGIN
psql:sql/verify_integrity.sql:74: ERROR:  duplicate key value violates unique constraint "product_sku_key"
DETAIL:  Key (sku)=(SKU-00001) already exists.
ROLLBACK

-- N3: duplicate unique email                   [expect: 23505 unique_violation]
BEGIN
psql:sql/verify_integrity.sql:81: ERROR:  duplicate key value violates unique constraint "app_user_email_key"
DETAIL:  Key (email)=(admin@mosaiq-demo.com) already exists.
ROLLBACK

-- N4: orphan foreign key, product -> category  [expect: 23503 foreign_key_violation]
BEGIN
psql:sql/verify_integrity.sql:88: ERROR:  insert or update on table "product" violates foreign key constraint "product_category_id_fkey"
DETAIL:  Key (category_id)=(999) is not present in table "category".
ROLLBACK

-- N5: orphan foreign key, transaction -> customer [expect: 23503 foreign_key_violation]
BEGIN
psql:sql/verify_integrity.sql:95: ERROR:  insert or update on table "transaction" violates foreign key constraint "transaction_customer_id_fkey"
DETAIL:  Key (customer_id)=(99999999-9999-9999-9999-999999999999) is not present in table "customer".
ROLLBACK

-- N6: CHECK, negative price                    [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:102: ERROR:  new row for relation "product" violates check constraint "product_list_price_check"
DETAIL:  Failing row contains (9004, SKU-09004, Negative price, 1, -1.00, null, t).
ROLLBACK

-- N7: CHECK, email without @                   [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:109: ERROR:  new row for relation "app_user" violates check constraint "app_user_email_check"
DETAIL:  Failing row contains (4b115005-3668-445e-af0b-506e3fe2b9a2, 2, Bad email, not-an-email, x, t, 2026-09-06 01:02:30.319545+00).
ROLLBACK

-- N8: CHECK, campaign status outside the domain [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:116: ERROR:  new row for relation "campaign" violates check constraint "campaign_status_check"
DETAIL:  Failing row contains (9005, Bad status, 1, 2026-01-01, 2026-02-01, PAUSED).
ROLLBACK

-- N9: CHECK, RFM band outside 1..5             [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:123: ERROR:  new row for relation "segment_rule" violates check constraint "segment_rule_r_max_check"
DETAIL:  Failing row contains (9006, RULE_BAD, 9, 9, 1, 3, 1, 3).
ROLLBACK

-- N10: CHECK, quantity must be positive        [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:130: ERROR:  new row for relation "transaction_line" violates check constraint "transaction_line_quantity_check"
DETAIL:  Failing row contains (2, 39, 0, 10.00).
ROLLBACK

-- N11: CHECK, campaign ending before it starts [expect: 23514 check_violation]
BEGIN
psql:sql/verify_integrity.sql:137: ERROR:  new row for relation "campaign" violates check constraint "campaign_check"
DETAIL:  Failing row contains (9007, Ends before it starts, 1, 2026-03-01, 2026-01-01, DRAFT).
ROLLBACK

-- N12: NOT NULL, product without a name        [expect: 23502 not_null_violation]
BEGIN
psql:sql/verify_integrity.sql:144: ERROR:  null value in column "name" of relation "product" violates not-null constraint
DETAIL:  Failing row contains (9008, SKU-09008, null, 1, 10.00, null, t).
ROLLBACK

-- N13: RESTRICT, deleting a category with products [expect: 23503 foreign_key_violation]
BEGIN
psql:sql/verify_integrity.sql:152: ERROR:  update or delete on table "category" violates foreign key constraint "product_category_id_fkey" on table "product"
DETAIL:  Key (category_id)=(11) is still referenced from table "product".
ROLLBACK

-- N16: RESTRICT, deleting a parent category    [expect: 23503 foreign_key_violation]
BEGIN
psql:sql/verify_integrity.sql:160: ERROR:  update or delete on table "category" violates foreign key constraint "category_parent_category_id_fkey" on table "category"
DETAIL:  Key (category_id)=(1) is still referenced from table "category".
ROLLBACK

-- N14: RESTRICT, deleting a customer with sales [expect: 23503 foreign_key_violation]
BEGIN
psql:sql/verify_integrity.sql:166: ERROR:  update or delete on table "customer" violates foreign key constraint "transaction_customer_id_fkey" on table "transaction"
DETAIL:  Key (customer_id)=(00000000-0000-0000-0000-000000000001) is still referenced from table "transaction".
ROLLBACK

-- N15: duplicate composite key                 [expect: 23505 unique_violation]
BEGIN
psql:sql/verify_integrity.sql:172: ERROR:  duplicate key value violates unique constraint "inventory_pkey"
DETAIL:  Key (store_id, product_id)=(1, 1) already exists.
ROLLBACK

==============================================
Volume check — at least 30 rows per table
role and channel are exempt: see sql/seed-exempt.txt
==============================================
         table_name         | exact_rows 
----------------------------+------------
 app_user                   |         30
 audit_log                  |        280
 campaign                   |         30
 category                   |         30
 channel                    |          5
 customer                   |         30
 customer_interest_category |         60
 customer_preferred_channel |         60
 experiment                 |         30
 experiment_group           |         60
 experiment_group_customer  |        120
 inventory                  |        150
 product                    |         40
 role                       |          7
 segment                    |         30
 segment_rule               |         30
 store                      |         30
 transaction                |        300
 transaction_line           |        600
(19 rows)

```
