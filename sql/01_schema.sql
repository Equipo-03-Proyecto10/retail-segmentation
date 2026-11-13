-- =========================================================
-- 01_schema.sql
-- MOSAIQ — logical model in 4NF: tables, keys, constraints, indexes.
--
-- Runs against the database created by 00_create_database.sql. Every
-- normalization decision behind these tables is written up in
-- docs/data-model.md; this file is the model, not the argument for it.
--
-- This is the only place DDL is written. A table is never altered by hand on
-- the server, and a schema change that breaks a clean run of the three
-- scripts in order is a broken change.
-- =========================================================

-- ---------- CATALOGS ----------

CREATE TABLE role (
    role_id     SMALLINT PRIMARY KEY,
    code        VARCHAR(40)  NOT NULL UNIQUE,
    description VARCHAR(160),
    -- Permission keys are deliberately ASCII and upper-case.  In particular,
    -- a case variant or a Unicode look-alike must never sit beside ADMIN and
    -- confuse operators while receiving no matrix permissions.
    CONSTRAINT role_code_format CHECK (code ~ '^[A-Z][A-Z0-9_]*$'),
    CONSTRAINT role_admin_code_bound_to_reserved_id
        CHECK (code <> 'ADMIN' OR role_id = 1),
    CONSTRAINT role_admin_lookalike CHECK (
        code = 'ADMIN' OR code !~ '^ADM([1IL]N|N)$'
    )
);

-- RN-01 (#252): a role's code never changes. The permission matrix in
-- web/middleware/authz.py is keyed by the code, and the single-administrator
-- index below is keyed by role_id = 1; renaming codes would let the two drift
-- apart, handing ADMIN's permissions to another role_id that no index guards.
-- SQLSTATE 23514 and the constraint name let the application translate the
-- refusal like any other CHECK.
CREATE OR REPLACE FUNCTION fn_role_code_immutable() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION 'role % keeps its code %', OLD.role_id, OLD.code
            USING ERRCODE = 'check_violation', CONSTRAINT = 'role_code_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_role_code_immutable
    BEFORE UPDATE OF code ON role
    FOR EACH ROW EXECUTE FUNCTION fn_role_code_immutable();

CREATE TABLE channel (
    channel_id SMALLINT PRIMARY KEY,
    name       VARCHAR(60) NOT NULL UNIQUE
);

CREATE TABLE category (
    category_id        SMALLINT PRIMARY KEY,
    name               VARCHAR(80) NOT NULL UNIQUE,
    parent_category_id SMALLINT REFERENCES category(category_id) ON DELETE RESTRICT,
    -- RN-33: the hierarchy is a tree. The CHECK refuses the one-row cycle;
    -- trg_category_no_cycle below refuses the longer ones (#253).
    CONSTRAINT category_not_own_parent CHECK (parent_category_id <> category_id)
);

-- RN-33: a category may not sit under one of its own subcategories. A CHECK
-- cannot see other rows, so the walk up from the new parent is a trigger.
-- The advisory lock serializes hierarchy changes: without it, two concurrent
-- moves (A under B, B under A) could each pass against the other's old state.
-- SQLSTATE 23514 and the constraint name let the application translate the
-- refusal like any other CHECK.
CREATE OR REPLACE FUNCTION fn_category_no_cycle() RETURNS TRIGGER AS $$
BEGIN
    IF NEW.parent_category_id IS NULL THEN
        RETURN NEW;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtext('category_hierarchy'));
    IF EXISTS (
        WITH RECURSIVE ancestor(category_id) AS (
            SELECT NEW.parent_category_id
            UNION
            SELECT c.parent_category_id
            FROM category AS c
            JOIN ancestor AS a ON a.category_id = c.category_id
            WHERE c.parent_category_id IS NOT NULL
        )
        SELECT 1 FROM ancestor WHERE category_id = NEW.category_id
    ) THEN
        RAISE EXCEPTION 'category % cannot sit under its own subcategory %',
            NEW.category_id, NEW.parent_category_id
            USING ERRCODE = 'check_violation', CONSTRAINT = 'category_no_cycle';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_category_no_cycle
    BEFORE INSERT OR UPDATE OF parent_category_id ON category
    FOR EACH ROW EXECUTE FUNCTION fn_category_no_cycle();

-- #586: the row trigger above takes the advisory lock after the statement has
-- already locked its row, so two opposite moves (A under B, B under A) each
-- held one row and waited on the other's row lock behind the advisory lock: a
-- deadlock, answered as 503. A BEFORE STATEMENT trigger runs before any row is
-- touched, so every hierarchy writer takes the lock first and the loser waits
-- with nothing held, then fails the walk with category_no_cycle (409). The
-- lock is reentrant within the transaction, so the row trigger's own call
-- stays as a no-op safety net.
CREATE OR REPLACE FUNCTION fn_category_hierarchy_lock() RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_advisory_xact_lock(hashtext('category_hierarchy'));
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_category_hierarchy_lock
    BEFORE INSERT OR UPDATE OF parent_category_id ON category
    FOR EACH STATEMENT EXECUTE FUNCTION fn_category_hierarchy_lock();

CREATE TABLE store (
    store_id  SMALLINT PRIMARY KEY,
    name      VARCHAR(100) NOT NULL,
    city      VARCHAR(80)  NOT NULL,
    state     VARCHAR(80)  NOT NULL,
    is_active BOOLEAN      NOT NULL DEFAULT TRUE
);

-- The RFM bands a segment is defined by. Kept as data rather than as code so
-- that an administrator can retune a segment without a deployment.
CREATE TABLE segment_rule (
    rule_id   INT PRIMARY KEY,
    rule_code VARCHAR(40) NOT NULL UNIQUE,
    r_min SMALLINT NOT NULL CHECK (r_min BETWEEN 1 AND 5),
    r_max SMALLINT NOT NULL CHECK (r_max BETWEEN 1 AND 5),
    f_min SMALLINT NOT NULL CHECK (f_min BETWEEN 1 AND 5),
    f_max SMALLINT NOT NULL CHECK (f_max BETWEEN 1 AND 5),
    m_min SMALLINT NOT NULL CHECK (m_min BETWEEN 1 AND 5),
    m_max SMALLINT NOT NULL CHECK (m_max BETWEEN 1 AND 5),
    CHECK (r_min <= r_max AND f_min <= f_max AND m_min <= m_max)
);

-- ---------- SEGMENTATION ----------

-- The stable, ordered label vocabulary every segmentation run's assignments
-- must draw from — ADR-0018. ordinal_position is unique so K-means centroids
-- can be paired deterministically with labels in declared best-to-worst
-- business order; it is not a foreign key target itself. Declared before
-- segment so segment.label_code can reference it.
CREATE TABLE segment_label (
    label_code       VARCHAR(40) PRIMARY KEY,
    ordinal_position  SMALLINT NOT NULL UNIQUE CHECK (ordinal_position > 0),
    name              VARCHAR(80) NOT NULL,
    description       VARCHAR(255)
);

CREATE TABLE segment (
    segment_id  INT PRIMARY KEY,
    name        VARCHAR(80) NOT NULL UNIQUE,
    description VARCHAR(255),
    rule_id     INT NOT NULL REFERENCES segment_rule(rule_id) ON DELETE RESTRICT,
    label_code  VARCHAR(40) NOT NULL REFERENCES segment_label(label_code) ON DELETE RESTRICT,
    valid_from  DATE NOT NULL,
    valid_to    DATE,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    UNIQUE (segment_id, label_code)
);

-- ---------- USERS AND CUSTOMERS ----------

-- app_user, not user: user is a reserved word in PostgreSQL and would have to
-- be double-quoted at every mention.
CREATE TABLE app_user (
    user_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    role_id       SMALLINT NOT NULL REFERENCES role(role_id) ON DELETE RESTRICT,
    name          VARCHAR(120) NOT NULL,
    email         VARCHAR(160) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT app_user_email_check CHECK (
        email ~ '^[^@[:space:]]+@[^@[:space:].]+(\.[^@[:space:].]+)+$'
    )
);

-- Server-side sessions (ADR-0022, #251). The signed cookie carries only
-- session_id; every request re-reads the row, the user's is_active and role,
-- so signing out (RF-02) and deactivation (RF-09) take effect on the very next
-- request. Runtime state, not business data: not audited (RN-28 does not list
-- it) and seed-exempt, since seeding it would invent sign-ins.
CREATE TABLE app_session (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id    UUID NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    CONSTRAINT app_session_last_seen_after_created CHECK (last_seen_at >= created_at),
    CONSTRAINT app_session_revoked_after_created CHECK (revoked_at >= created_at)
);

-- Deactivation revokes every open session of one user.
CREATE INDEX idx_app_session_open_by_user
    ON app_session (user_id) WHERE revoked_at IS NULL;

-- The schema half of the single-administrator rule (RN-01, F4-02). Every row
-- the predicate admits holds the same role_id, so uniqueness over that column
-- admits exactly one of them.
--
-- The literal 1 is a coupling, not a magic number: an index predicate must be
-- immutable, so it cannot look ADMIN up by its code in `role`, and this index
-- therefore depends on 02_seed_30_per_table.sql giving ADMIN role_id 1.
-- tests/test_single_administrator.py asserts the seed and this predicate still
-- agree, so renumbering the roles fails the build instead of quietly leaving
-- the rule unenforced.
--
-- It constrains how many rows hold role_id = 1, not what else those rows say:
-- renaming the administrator, rotating their password (F4-06, #107) and
-- deactivating them are all still possible. Refusing to leave the system with
-- *zero* administrators is the other direction of RN-01, which no unique index
-- can express; web/services/users.py holds that half.
--
-- AGENTS.md is explicit that one half alone does not count: the application
-- check alone is bypassed by a direct INSERT, and the index alone surfaces as
-- an unexplained database error.
CREATE UNIQUE INDEX ux_app_user_single_administrator
    ON app_user (role_id) WHERE role_id = 1;

-- One account per mailbox, whatever the letter case (#288). A plain UNIQUE on
-- email treated USER2@ and user2@ as different people, so an administrator
-- could create three accounts for one address. The application lower-cases
-- the address before writing and when signing in (web/services/users.py,
-- web/db/users.py); this index is the half a direct INSERT cannot bypass.
CREATE UNIQUE INDEX ux_app_user_email_lower ON app_user (lower(email));

CREATE TABLE customer (
    customer_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID UNIQUE REFERENCES app_user(user_id) ON DELETE SET NULL,
    name                    VARCHAR(120) NOT NULL,
    email                   VARCHAR(160),
    phone                   VARCHAR(20),
    registration_channel_id SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    registered_on           DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT customer_email_check CHECK (
        email IS NULL OR
        email ~ '^[^@[:space:]]+@[^@[:space:].]+(\.[^@[:space:].]+)+$'
    )
);

-- The two tables the 4NF decomposition produced. A customer's preferred
-- channels and their categories of interest are independent multivalued facts:
-- holding both in one table forces a row per combination and invents pairings
-- the business never stated. docs/data-model.md carries the full argument.
CREATE TABLE customer_preferred_channel (
    customer_id UUID NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    channel_id  SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE CASCADE,
    PRIMARY KEY (customer_id, channel_id)
);

CREATE TABLE customer_interest_category (
    customer_id UUID NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    category_id SMALLINT NOT NULL REFERENCES category(category_id) ON DELETE CASCADE,
    PRIMARY KEY (customer_id, category_id)
);


-- ---------- SEGMENTATION HISTORY (F7-02, replaces the mutable segment column) ----------

-- One row per recalculation. method is fixed to RFM_RULES today; KMEANS is
-- reserved for the Stage 3 adapter ADR-0018 describes, not produced yet.
-- parameters, customer_count and executed_by are ADR-0017's "parameter
-- snapshot, produced customer count, and executing user" -- executed_by is
-- nullable for the same reason audit_log.user_id is: a seed or maintenance
-- run has no real actor.
CREATE TABLE segmentation_run (
    run_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method         VARCHAR(20) NOT NULL CHECK (method IN ('RFM_RULES', 'KMEANS')),
    window_days    INT NOT NULL CHECK (window_days > 0),
    parameters     JSONB NOT NULL DEFAULT '{}'::jsonb,
    customer_count INT NOT NULL DEFAULT 0,
    executed_by    UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- ADR-0052: SEEDED if any sale the run scored is SEEDED, else OBSERVED,
    -- recorded when the run executes. A later backdated CSV load adds sales
    -- to an old window, so it is a historical fact, not derivable afterwards.
    -- No default: every writer states it. create_run takes it from
    -- fn_segmentation_window_origin, and trg_segmentation_run_origin_guard
    -- (DATA ORIGIN below) refuses an OBSERVED run over a SEEDED sale. Last
    -- column, so an instance that gained it by ADD COLUMN dumps the same.
    data_origin    VARCHAR(20) NOT NULL CHECK (data_origin IN ('OBSERVED', 'SEEDED'))
);

-- A run is a record of what was computed and how, and the application only
-- ever adds one (#350, ADR-0029). Rewriting or deleting it would change the
-- provenance of every assignment that names it, and DELETE would cascade to
-- them as the owner. Maintenance stays with the schema owner.
REVOKE UPDATE, DELETE ON segmentation_run FROM retail_app;

-- Durable assignment history — ADR-0017 and ADR-0018. A run never overwrites
-- a customer's row; it closes the open one (valid_to) and opens a new one.
-- segment_id and label_code are both NULL for a customer with no sales in
-- the scored window: RN-21, an unassigned result is recorded, never skipped.
--
-- segment_id is kept alongside label_code (ADR-0018's stable vocabulary,
-- what migration/dashboard/recommendation consumers read) rather than
-- replaced by it: web/db/customers.py's list_customers_in_segment and
-- web/routes/catalog.py's customer_detail (F3-05, already shipped) browse
-- by the exact matched RFM band, and several segments can legitimately
-- share one label_code -- collapsing to label_code alone would change which
-- customers "browse segment N" shows. label_code is redundant with
-- segment_id (segment.label_code) but kept denormalized here so a report
-- reads the stable label directly instead of joining through segment, whose
-- rows can later be retired while history must not lose its label. The
-- composite foreign key enforces that a real segment_id carries that
-- segment's label; its PostgreSQL 15+ column-list action, ON DELETE SET NULL
-- (segment_id), retires only the band reference. The separate label_code
-- foreign key enforces ADR-0018's vocabulary even when MATCH SIMPLE skips
-- the composite check because segment_id is NULL, as future KMEANS rows are.
--
-- ADR-0017 requires one result per customer in each run, enforced by
-- UNIQUE (run_id, customer_id). The partial open-row index cannot provide
-- that guarantee because a closed duplicate is outside its predicate.
-- run_id leads the constraint so its backing index also serves per-run reads
-- and cascaded deletes; the separate customer-first index serves history.
CREATE TABLE customer_segment_history (
    history_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id UUID NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    run_id      BIGINT NOT NULL REFERENCES segmentation_run(run_id) ON DELETE CASCADE,
    segment_id  INT,
    label_code  VARCHAR(40),
    FOREIGN KEY (segment_id, label_code)
        REFERENCES segment (segment_id, label_code)
        ON DELETE SET NULL (segment_id),
    FOREIGN KEY (label_code)
        REFERENCES segment_label (label_code) ON DELETE RESTRICT,
    -- Raw recency, frequency and monetary values (ADR-0017), alongside their
    -- quintile scores below.
    recency_last_purchase_at TIMESTAMPTZ,
    frequency_count          INT,
    monetary_total           NUMERIC(12,2),
    r_score     SMALLINT,
    f_score     SMALLINT,
    m_score     SMALLINT,
    valid_from  TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_to    TIMESTAMPTZ,
    CHECK (valid_to IS NULL OR valid_to >= valid_from),
    -- Quintile scores run 1 to 5 (RN-18 states the same range for the bands),
    -- and a count or a total of money is never negative. NULL passes: an
    -- unassigned customer, and a K-means row, carry no scores (RN-21).
    CHECK (r_score BETWEEN 1 AND 5),
    CHECK (f_score BETWEEN 1 AND 5),
    CHECK (m_score BETWEEN 1 AND 5),
    CHECK (frequency_count >= 0),
    CHECK (monetary_total >= 0),
    UNIQUE (run_id, customer_id),
    -- One customer is never in two segments at once, in the past as well as
    -- now: the open-row index below only guards the present. Ranges are
    -- half-open, so a row closed at the instant the next opens is adjacent, not
    -- overlapping. Needs btree_gist for the uuid equality (00_create_database.sql).
    CONSTRAINT ex_customer_segment_history_no_overlap
        EXCLUDE USING gist (
            customer_id WITH =,
            tstzrange(valid_from, valid_to) WITH &&
        )
);

-- Exactly one open row per customer — the partial unique index a concurrent
-- writer collides against, not just an application-level check.
CREATE UNIQUE INDEX ux_customer_segment_history_open
    ON customer_segment_history (customer_id) WHERE valid_to IS NULL;

CREATE INDEX idx_customer_segment_history_customer
    ON customer_segment_history (customer_id, valid_from);

-- A history row is never updated in place (ADR-0017); the one thing a run does
-- to an existing row is close it. So the application role loses UPDATE and
-- DELETE and gets back UPDATE on valid_to alone, and a trigger applies the same
-- rule to everyone else: valid_to may be set once, on an open row, and nothing
-- else about a row may change. The single exception is segment_id going NULL,
-- which is the foreign key's own ON DELETE SET NULL (segment_id) retiring a band
-- reference while the row keeps its label. Deleting a customer or a run still
-- cascades, as the owner. #350, ADR-0029.
REVOKE UPDATE, DELETE ON customer_segment_history FROM retail_app;
GRANT UPDATE (valid_to) ON customer_segment_history TO retail_app;

CREATE FUNCTION fn_customer_segment_history_close_only()
RETURNS TRIGGER AS $$
BEGIN
    IF (to_jsonb(NEW) - 'valid_to' - 'segment_id')
           IS DISTINCT FROM (to_jsonb(OLD) - 'valid_to' - 'segment_id')
       OR (NEW.segment_id IS DISTINCT FROM OLD.segment_id
           AND NEW.segment_id IS NOT NULL)
       OR (NEW.valid_to IS DISTINCT FROM OLD.valid_to
           AND OLD.valid_to IS NOT NULL) THEN
        RAISE EXCEPTION 'Segment history row % may only be closed, once', OLD.history_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'customer_segment_history_close_only';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_customer_segment_history_close_only
BEFORE UPDATE ON customer_segment_history
FOR EACH ROW EXECUTE FUNCTION fn_customer_segment_history_close_only();

-- ---------- PRODUCT CATALOG AND SALES ----------

CREATE TABLE product (
    product_id  INT PRIMARY KEY,
    sku         VARCHAR(40) NOT NULL UNIQUE,
    name        VARCHAR(150) NOT NULL,
    category_id SMALLINT NOT NULL REFERENCES category(category_id) ON DELETE RESTRICT,
    list_price  NUMERIC(10,2) NOT NULL CHECK (list_price >= 0),
    -- F3-07 stores the uploaded file under UPLOAD_DIR and keeps only its path
    -- here. The bytes are never versioned and never held in the database.
    image_path  VARCHAR(255),
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE transaction (
    transaction_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The identifier the sales contract supplies (ADR-0020), durable and
    -- distinct from the surrogate key above. UNIQUE so a file re-sent in
    -- full is rejected as a duplicate rather than inserted a second time;
    -- NOT NULL because a row whose identifier is absent is rejected for
    -- that reason, not defaulted. Global UNIQUE is enough while CSV is the
    -- sole producer (ADR-0020); docs/data-model.md §4 records what a later
    -- multi-producer contract would need to change here. The prefix
    -- `svc-sales:` is reserved for sales-service (ADR-0039): the guards in
    -- BOUNDARY sales refuse it from any other session user, refuse
    -- anything else from svc_sales, and refuse a svc_sales row whose
    -- data_origin is not OBSERVED (ADR-0052).
    source_transaction_id  VARCHAR(64) NOT NULL UNIQUE,
    customer_id            UUID NOT NULL REFERENCES customer(customer_id) ON DELETE RESTRICT,
    store_id               SMALLINT NOT NULL REFERENCES store(store_id) ON DELETE RESTRICT,
    channel_id             SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    occurred_at            TIMESTAMPTZ NOT NULL,
    total                  NUMERIC(12,2) NOT NULL CHECK (total >= 0),
    -- ADR-0052: where the sale came from. The seed writes SEEDED; the CSV
    -- import (ADR-0020) and sales-service (ADR-0039) write OBSERVED, and the
    -- guard in BOUNDARY sales refuses anything else from svc_sales. No
    -- default, so a writer that forgets it fails rather than guessing. On the
    -- header, not the lines: a line belongs to one sale, and the CSV import
    -- refuses a line onto a SEEDED sale. Last column, so an instance that
    -- gained it by ADD COLUMN dumps the same.
    data_origin            VARCHAR(20) NOT NULL CHECK (data_origin IN ('OBSERVED', 'SEEDED'))
);

CREATE TABLE transaction_line (
    transaction_id BIGINT NOT NULL REFERENCES transaction(transaction_id) ON DELETE CASCADE,
    product_id     INT NOT NULL REFERENCES product(product_id) ON DELETE RESTRICT,
    quantity       INT NOT NULL CHECK (quantity > 0),
    unit_price     NUMERIC(10,2) NOT NULL CHECK (unit_price > 0),
    PRIMARY KEY (transaction_id, product_id)
);

-- BEGIN DATA ORIGIN COLUMNS (ADR-0052, #559)
-- A no-op on a clean build, where CREATE TABLE above already has both
-- columns. On a database loaded before #559 it adds them nullable, so the
-- operator's backfill (deploy/README.md) can fill them before the block
-- below makes them NOT NULL. DDL stays in this file (AGENTS.md, ADR-0046).
ALTER TABLE transaction ADD COLUMN IF NOT EXISTS data_origin VARCHAR(20);
ALTER TABLE segmentation_run ADD COLUMN IF NOT EXISTS data_origin VARCHAR(20);
-- END DATA ORIGIN COLUMNS

-- BEGIN DATA ORIGIN (ADR-0052, #559)
-- Re-runnable, so an operator can apply it to a database that gained the
-- columns from the block above (deploy/README.md). There, SET NOT NULL
-- refuses to finish while any row is left unfilled, and each CHECK is added
-- under the name CREATE TABLE gives it on a clean build, so the two dump the
-- same (deploy/check-schema-drift.sh). On a clean build all four are no-ops.
ALTER TABLE transaction ALTER COLUMN data_origin SET NOT NULL;
ALTER TABLE segmentation_run ALTER COLUMN data_origin SET NOT NULL;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'public.transaction'::regclass
      AND conname = 'transaction_data_origin_check'
) THEN ALTER TABLE public.transaction ADD CONSTRAINT transaction_data_origin_check
        CHECK (data_origin IN ('OBSERVED', 'SEEDED')); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'public.segmentation_run'::regclass
      AND conname = 'segmentation_run_data_origin_check'
) THEN ALTER TABLE public.segmentation_run ADD CONSTRAINT segmentation_run_data_origin_check
        CHECK (data_origin IN ('OBSERVED', 'SEEDED')); END IF; END $$;

-- A segmentation run records whether any sale it scored was SEEDED. The
-- window is the one the scoring reads (web/db/segments.py): occurred_at in
-- [now() - window_days, now()], now() being the run transaction's start, so
-- the run's INSERT and the scoring see the same sales.
CREATE OR REPLACE FUNCTION fn_segmentation_window_origin(window_days INT)
RETURNS VARCHAR(20)
LANGUAGE sql
STABLE
AS $$
    SELECT CASE WHEN EXISTS (
        SELECT FROM public.transaction AS t
         WHERE t.data_origin = 'SEEDED'
           AND t.occurred_at >= now() - make_interval(days => window_days)
           AND t.occurred_at <= now()
    ) THEN 'SEEDED' ELSE 'OBSERVED' END::VARCHAR(20)
$$;

REVOKE EXECUTE ON FUNCTION fn_segmentation_window_origin(INT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION fn_segmentation_window_origin(INT) TO retail_app;

-- The schema, not only create_run, keeps a run that read a SEEDED sale from
-- being recorded OBSERVED. One direction only, on INSERT only: a SEEDED run
-- over OBSERVED sales over-marks, which is the safe error, and the seed's
-- SEEDED runs load whatever order the sales arrive in.
CREATE OR REPLACE FUNCTION fn_segmentation_run_origin_guard()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.data_origin = 'OBSERVED' THEN
        IF fn_segmentation_window_origin(NEW.window_days) = 'SEEDED' THEN
            RAISE EXCEPTION 'Run over a window holding a SEEDED sale cannot be OBSERVED'
                USING ERRCODE = '23514',
                      CONSTRAINT = 'segmentation_run_origin_observed_only';
        END IF;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE OR REPLACE TRIGGER trg_segmentation_run_origin_guard
BEFORE INSERT ON segmentation_run
FOR EACH ROW EXECUTE FUNCTION fn_segmentation_run_origin_guard();

-- Serves fn_segmentation_window_origin: SEEDED sales are a small, fixed set,
-- so the partial index stays small however many OBSERVED sales arrive.
CREATE INDEX IF NOT EXISTS idx_transaction_seeded_date
    ON transaction (occurred_at) WHERE data_origin = 'SEEDED';
-- END DATA ORIGIN

-- ---------- SALES CSV IMPORT (F8-01, #334) ----------
--
-- One row per upload attempt, so the administrator's rejection report stays
-- retrievable after the page is left (AC 3) instead of living only in the
-- HTTP response that produced it.
CREATE TABLE sales_load (
    load_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filename          VARCHAR(255) NOT NULL,
    contract_version  INT NOT NULL,
    received_count    INT NOT NULL CHECK (received_count >= 0),
    accepted_count    INT NOT NULL CHECK (accepted_count >= 0),
    rejected_count    INT NOT NULL CHECK (rejected_count >= 0),
    loaded_by         UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (received_count = accepted_count + rejected_count)
);

-- One row per rejected line, with the file's own line number (the header is
-- line 1, so the first data row is line 2) -- not the record index, which
-- would be off by one against what the administrator sees in a spreadsheet.
CREATE TABLE sales_load_rejection (
    rejection_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    load_id      BIGINT NOT NULL REFERENCES sales_load(load_id) ON DELETE CASCADE,
    line_number  INT NOT NULL CHECK (line_number > 0),
    reason       VARCHAR(255) NOT NULL
);

CREATE INDEX idx_sales_load_rejection_load ON sales_load_rejection (load_id);

-- ---------- DOMAIN CHECKS THE TABLES ABOVE CANNOT STATE INLINE (#591) ----------
--
-- The application and the services already refuse these; these constraints make
-- the database refuse them from any writer. Dates compare with the clock, which
-- a CHECK may read: a row that passed once can never fail later, because time
-- only moves forward, so a restore or a re-check of an old row stays valid.
-- clock_timestamp() rather than now() so a sale stamped just after the
-- transaction began is not called future.
ALTER TABLE customer
    ADD CONSTRAINT customer_registered_not_future CHECK (registered_on <= CURRENT_DATE);
ALTER TABLE transaction
    ADD CONSTRAINT transaction_not_future CHECK (occurred_at <= clock_timestamp());
ALTER TABLE segmentation_run
    ADD CONSTRAINT segmentation_run_customer_count_check CHECK (customer_count >= 0),
    ADD CONSTRAINT segmentation_run_parameters_object
        CHECK (jsonb_typeof(parameters) = 'object');

-- RN-37: a header total is derived from its lines. A row CHECK cannot see the
-- other table, and the header is written before its lines, so the comparison
-- is a deferred constraint trigger: it runs at COMMIT, once the header, its
-- lines and the recompute are all in. A header with no lines must total 0.
-- Statement order inside a transaction is free; only the committed state is
-- held to the rule. SQLSTATE 23514 and the constraint name let a caller
-- translate it like any other CHECK.
-- Moving a line between sales changes both sums, so an UPDATE that changes
-- transaction_id checks both the source and destination headers.
CREATE OR REPLACE FUNCTION fn_transaction_total_matches_lines() RETURNS TRIGGER AS $$
DECLARE
    sale_id BIGINT;
    sale_ids BIGINT[];
BEGIN
    IF TG_TABLE_NAME = 'transaction' THEN
        sale_ids := ARRAY[NEW.transaction_id];
    ELSIF TG_OP = 'DELETE' THEN
        sale_ids := ARRAY[OLD.transaction_id];
    ELSIF TG_OP = 'UPDATE'
          AND OLD.transaction_id IS DISTINCT FROM NEW.transaction_id THEN
        sale_ids := ARRAY[OLD.transaction_id, NEW.transaction_id];
    ELSE
        sale_ids := ARRAY[NEW.transaction_id];
    END IF;
    FOREACH sale_id IN ARRAY sale_ids LOOP
        -- A cascade can delete the header before this runs; nothing is left to check.
        IF EXISTS (
            SELECT 1
            FROM transaction AS t
            WHERE t.transaction_id = sale_id
              AND t.total IS DISTINCT FROM (
                  SELECT COALESCE(SUM(l.quantity * l.unit_price), 0)
                  FROM transaction_line AS l
                  WHERE l.transaction_id = t.transaction_id
              )
        ) THEN
            RAISE EXCEPTION 'transaction % total differs from the sum of its lines', sale_id
                USING ERRCODE = 'check_violation',
                      CONSTRAINT = 'transaction_total_matches_lines';
        END IF;
    END LOOP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER trg_transaction_total_matches_lines
    AFTER INSERT OR UPDATE OF total ON transaction
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_transaction_total_matches_lines();

CREATE CONSTRAINT TRIGGER trg_transaction_line_total_matches_lines
    AFTER INSERT OR UPDATE OR DELETE ON transaction_line
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION fn_transaction_total_matches_lines();

-- ---------- CAMPAIGNS AND EXPERIMENTS ----------

CREATE TABLE campaign (
    campaign_id INT PRIMARY KEY,
    name        VARCHAR(120) NOT NULL,
    label_code  VARCHAR(40) NOT NULL REFERENCES segment_label(label_code) ON DELETE RESTRICT,
    starts_on   DATE NOT NULL,
    ends_on     DATE NOT NULL,
    status      VARCHAR(20) NOT NULL CHECK (status IN ('DRAFT','ACTIVE','FINISHED','CANCELLED')),
    CHECK (ends_on >= starts_on)
);

CREATE TABLE experiment (
    experiment_id          INT PRIMARY KEY,
    name                   VARCHAR(120) NOT NULL,
    campaign_id            INT REFERENCES campaign(campaign_id) ON DELETE SET NULL,
    target_metric          VARCHAR(60) NOT NULL,
    starts_on              DATE NOT NULL,
    ends_on                DATE,
    -- ADR-0019: fixed before the run starts, immutable after the first
    -- assignment. The schema can only require positivity; F11-04's service
    -- locks it once assignment begins.
    conversion_window_days SMALLINT NOT NULL CHECK (conversion_window_days > 0),
    -- OBSERVED is a real experiment over real assignments. SEEDED is
    -- ADR-0019's A/A validation; INJECTED is its fixed-seed synthetic-uplift
    -- fixture. F11-07 renders the Synthetic label for the latter two.
    data_origin            VARCHAR(20) NOT NULL
                              CHECK (data_origin IN ('OBSERVED','SEEDED','INJECTED')),
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

-- The application never deletes an experiment (#350, ADR-0029). Foreign-key
-- cascades run as the owner, so revoking DELETE on the assignments alone did not
-- stop DELETE FROM experiment from removing the assignments, exposures and
-- conversions beneath it. UPDATE stays: an experiment is edited before its first
-- assignment.
REVOKE DELETE ON experiment FROM retail_app;

CREATE TABLE experiment_group (
    group_id      INT PRIMARY KEY,
    experiment_id INT NOT NULL REFERENCES experiment(experiment_id) ON DELETE CASCADE,
    kind          VARCHAR(20) NOT NULL CHECK (kind IN ('CONTROL','TREATMENT')),
    -- User-facing arm definition.  These values are fixed once assignment
    -- starts; the application refuses edits after that point.
    name          VARCHAR(120) NOT NULL CHECK (btrim(name) <> ''),
    treatment_description VARCHAR(500) NOT NULL CHECK (btrim(treatment_description) <> ''),
    -- Lets experiment_assignment's FK pin a row to both its group and that
    -- group's experiment at once, so an assignment can never claim a group
    -- belonging to a different experiment.
    UNIQUE (group_id, experiment_id)
);

-- At most one control group per experiment (ADR-0028), the same shape as
-- ux_app_user_single_administrator. At least one treatment group before
-- activation is not expressible here -- nothing forces a row to exist -- and
-- is F11-03's service-level check.
CREATE UNIQUE INDEX ux_experiment_one_control
    ON experiment_group (experiment_id) WHERE kind = 'CONTROL';

CREATE FUNCTION fn_experiment_group_definition_immutable()
RETURNS TRIGGER AS $$
BEGIN
    IF (NEW.kind, NEW.name, NEW.treatment_description)
       IS DISTINCT FROM (OLD.kind, OLD.name, OLD.treatment_description)
       AND EXISTS (
           SELECT 1 FROM experiment_assignment
            WHERE group_id = OLD.group_id
       ) THEN
        RAISE EXCEPTION 'Experiment group % is fixed after assignment', OLD.group_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'experiment_group_definition_immutable';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_experiment_group_definition_immutable
BEFORE UPDATE OF kind, name, treatment_description ON experiment_group
FOR EACH ROW EXECUTE FUNCTION fn_experiment_group_definition_immutable();

-- The same reason as on experiment: deleting a group cascades to its assignments.
REVOKE DELETE ON experiment_group FROM retail_app;

-- One durable row per customer assigned to an experiment (ADR-0019), before
-- any outcome is known. Replaces experiment_group_customer, whose
-- (group_id, customer_id) primary key let one customer enter two arms of
-- the same experiment as long as the group differed; experiment_id is
-- carried directly here so UNIQUE (experiment_id, customer_id) closes that
-- gap, and the composite foreign key stops experiment_id being spoofed.
CREATE TABLE experiment_assignment (
    assignment_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    experiment_id INT NOT NULL,
    group_id      INT NOT NULL,
    customer_id   UUID NOT NULL REFERENCES customer(customer_id) ON DELETE RESTRICT,
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (experiment_id, customer_id),
    FOREIGN KEY (group_id, experiment_id)
        REFERENCES experiment_group (group_id, experiment_id) ON DELETE CASCADE
);

-- Append-only for the application role (RN-42, #223, ADR-0026): an
-- assignment fixes the experiment's denominator before any outcome is known,
-- so moving a customer to another arm afterwards would rewrite the result.
-- The owner keeps every privilege, and foreign-key cascades run as the owner,
-- so only retail_app's own UPDATE and DELETE are refused. A row lock on this
-- table needs UPDATE; the application locks the experiment row instead.
REVOKE UPDATE, DELETE ON experiment_assignment FROM retail_app;

-- A later, separate event: an assigned customer may remain unexposed
-- (ADR-0019), so exposure is never folded into the assignment row. More
-- than one exposure per assignment is allowed -- "each later exposure" is
-- plural in the ADR -- so there is no uniqueness constraint here.
CREATE TABLE experiment_exposure (
    exposure_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id BIGINT NOT NULL REFERENCES experiment_assignment(assignment_id) ON DELETE CASCADE,
    exposed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ADR-0019 requires the schema, not only the service, to keep the control arm
-- unexposed. The trigger follows the assignment to its group instead of
-- copying `kind` onto the event, which would create a second value that could
-- disagree with experiment_group. It also covers an owner changing an
-- exposure's assignment_id; retail_app cannot update the row at all.
CREATE FUNCTION fn_experiment_exposure_treatment_only()
RETURNS TRIGGER AS $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM experiment_assignment AS a
          JOIN experiment_group AS g
            ON g.group_id = a.group_id
           AND g.experiment_id = a.experiment_id
         WHERE a.assignment_id = NEW.assignment_id
           AND g.kind = 'CONTROL'
    ) THEN
        RAISE EXCEPTION 'Control-group assignment % cannot be exposed', NEW.assignment_id
            USING ERRCODE = '23514',
                  CONSTRAINT = 'experiment_exposure_treatment_only';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_experiment_exposure_treatment_only
BEFORE INSERT OR UPDATE OF assignment_id ON experiment_exposure
FOR EACH ROW EXECUTE FUNCTION fn_experiment_exposure_treatment_only();

-- An exposure is a durable event, not mutable state (ADR-0019, ADR-0027).
-- The owner retains maintenance privileges and foreign-key cascades still run
-- as the owner; the application role can only read and append events.
REVOKE UPDATE, DELETE ON experiment_exposure FROM retail_app;

-- Conversion links an assignment to a qualifying sale rather than adding an
-- experiment column to transaction (ADR-0019). UNIQUE stops the same sale
-- being recorded as a conversion twice for the same assignment; it does not
-- limit an assignment to one conversion, since an average-ticket metric can
-- legitimately draw on more than one qualifying sale.
CREATE TABLE experiment_conversion (
    conversion_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id  BIGINT NOT NULL REFERENCES experiment_assignment(assignment_id) ON DELETE CASCADE,
    transaction_id BIGINT NOT NULL REFERENCES transaction(transaction_id) ON DELETE RESTRICT,
    converted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (assignment_id, transaction_id)
);
-- A conversion is a durable event too (ADR-0019, ADR-0050): evaluating again
-- only adds. The owner retains maintenance privileges and foreign-key cascades
-- still run as the owner; the application role can only read and append.
REVOKE UPDATE, DELETE ON experiment_conversion FROM retail_app;

-- ---------- INVENTORY ----------

CREATE TABLE inventory (
    store_id          SMALLINT NOT NULL REFERENCES store(store_id) ON DELETE CASCADE,
    product_id        INT NOT NULL REFERENCES product(product_id) ON DELETE CASCADE,
    quantity_on_hand  INT NOT NULL CHECK (quantity_on_hand >= 0),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, product_id)
);

-- ---------- NOTIFICATIONS (AN-04, #419, ADR-0047) ----------

-- One row per thing that happened that somebody should be told about: the
-- event, kept once however many people it reaches.
--
-- The subject is an exclusive arc, exactly one of four foreign keys. A
-- polymorphic (subject_type, subject_id) pair could not be a foreign key, so a
-- deleted load or run would leave an orphan and nothing would stop an unknown
-- type. The first three subjects are UNIQUE: an event is announced once, and
-- the lazy announcer for an experiment's end (ADR-0047) relies on
-- ON CONFLICT (experiment_id) DO NOTHING as the guard that two racing workers
-- cannot both pass. subject_user_id is not UNIQUE, because a user can be
-- deactivated, reactivated and deactivated again, and each deactivation is
-- its own event.
--
-- kind is derived from whichever subject is set, so it cannot disagree with
-- it. No message text is stored: the sentence is built from the subject row
-- when it is read, so a corrected fact is never contradicted by an old
-- sentence. Runtime state, like app_session: not audited, and it carries the
-- default DML grant. ON DELETE CASCADE is the owner's, since the application
-- never deletes a load, a run, an experiment or a user.
CREATE TABLE notification (
    notification_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_load_id   BIGINT UNIQUE REFERENCES sales_load(load_id) ON DELETE CASCADE,
    run_id          BIGINT UNIQUE REFERENCES segmentation_run(run_id) ON DELETE CASCADE,
    experiment_id   INT    UNIQUE REFERENCES experiment(experiment_id) ON DELETE CASCADE,
    subject_user_id UUID   REFERENCES app_user(user_id) ON DELETE CASCADE,
    kind            VARCHAR(30) GENERATED ALWAYS AS (
                        CASE
                            WHEN sales_load_id   IS NOT NULL THEN 'SALES_LOAD_FINISHED'
                            WHEN run_id          IS NOT NULL THEN 'SEGMENT_RUN_COMPLETED'
                            WHEN experiment_id   IS NOT NULL THEN 'EXPERIMENT_ENDED'
                            WHEN subject_user_id IS NOT NULL THEN 'USER_DEACTIVATED'
                        END
                    ) STORED NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT notification_one_subject CHECK (
        num_nonnulls(sales_load_id, run_id, experiment_id, subject_user_id) = 1
    )
);

-- Who the event was written for, and whether they have read it. The recipients
-- are the active users whose role held the delivery permission when the event
-- happened, a snapshot: a later role change neither adds nor removes a row
-- (the permission is checked again when the row is read). read_at is the only
-- fact that differs per recipient, which is why it lives here and not on the
-- event. A user's notifications go with the user, as app_session does.
CREATE TABLE notification_recipient (
    notification_id BIGINT NOT NULL REFERENCES notification(notification_id) ON DELETE CASCADE,
    user_id         UUID   NOT NULL REFERENCES app_user(user_id) ON DELETE CASCADE,
    read_at         TIMESTAMPTZ,
    PRIMARY KEY (notification_id, user_id)
);

-- The inbox, newest first: one user's rows by descending identity, which is
-- creation order. The primary key above serves the join back to the event and
-- the cascade from it.
CREATE INDEX idx_notification_recipient_inbox
    ON notification_recipient (user_id, notification_id DESC);

-- The unread count shown on every page, and "mark all read".
CREATE INDEX idx_notification_recipient_unread
    ON notification_recipient (user_id) WHERE read_at IS NULL;

-- ---------- AUDIT ----------

CREATE TABLE audit_log (
    audit_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id     UUID REFERENCES app_user(user_id) ON DELETE SET NULL,
    entity      VARCHAR(60) NOT NULL,
    entity_pk   VARCHAR(80) NOT NULL,
    action      VARCHAR(20) NOT NULL CHECK (action IN ('INSERT','UPDATE','DELETE')),
    data_before JSONB,
    data_after  JSONB,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only for the application role (RN-30, RNF-17, #290). The role that
-- owns the schema keeps every privilege, so archiving is still possible;
-- only retail_app loses UPDATE and DELETE. TRUNCATE was never granted. The
-- audit trigger only INSERTs, so it is unaffected. ADR-0024.
REVOKE UPDATE, DELETE ON audit_log FROM retail_app;

-- Grants on legacy tables to the distributed stage's roles. Each is also a
-- row of contracts/db/grants.csv, which contracts/db/check_grants.sh compares
-- with the database in both directions (ADR-0034).
--
-- The bench-only Rust build of the products reads: SELECT on product here, and
-- on the products boundary's four tables in that boundary's section; nothing
-- else (ADR-0042, ADR-0078).
GRANT SELECT ON product TO svc_products_bench;
-- fn_audit runs as definer_audit (AUDIT STRATEGY below), which may append to
-- the audit log and do nothing else.
GRANT INSERT ON audit_log TO definer_audit;

-- =========================================================
-- INDEXES
-- =========================================================

CREATE INDEX idx_transaction_customer_date        ON transaction (customer_id, occurred_at);
CREATE INDEX idx_transaction_store_date           ON transaction (store_id, occurred_at);
CREATE INDEX idx_transaction_line_product         ON transaction_line (product_id);
CREATE INDEX idx_product_category                 ON product (category_id);
CREATE INDEX idx_campaign_label                   ON campaign (label_code);
CREATE INDEX idx_experiment_assignment_customer   ON experiment_assignment (customer_id);
CREATE INDEX idx_experiment_exposure_assignment   ON experiment_exposure (assignment_id);
CREATE INDEX idx_audit_log_entity                 ON audit_log (entity, entity_pk);

-- Foreign keys whose child grows and whose parent is deleted by some code path
-- (the ON DELETE check would otherwise scan the child) or whose child is read
-- by that key (#596). docs/data-model.md §4 names the keys left unindexed and
-- why.
-- role is deletable; users are also listed and counted by role.
CREATE INDEX idx_app_user_role                    ON app_user (role_id);
-- channel is deletable: RESTRICT from customer and transaction, CASCADE from
-- customer_preferred_channel.
CREATE INDEX idx_customer_registration_channel    ON customer (registration_channel_id);
CREATE INDEX idx_customer_preferred_channel_channel ON customer_preferred_channel (channel_id);
CREATE INDEX idx_transaction_channel              ON transaction (channel_id);
-- category is deletable; children and subtrees are read by parent.
CREATE INDEX idx_category_parent                  ON category (parent_category_id);
CREATE INDEX idx_customer_interest_category_category ON customer_interest_category (category_id);
-- product is deletable (CASCADE); stock is read per product.
CREATE INDEX idx_inventory_product                ON inventory (product_id);
-- A campaign's experiments, an experiment's arms, and an arm's assignments.
CREATE INDEX idx_experiment_campaign              ON experiment (campaign_id);
CREATE INDEX idx_experiment_group_experiment      ON experiment_group (experiment_id);
CREATE INDEX idx_experiment_assignment_group      ON experiment_assignment (group_id, experiment_id);

-- =========================================================
-- AUDIT STRATEGY
--
-- One generic trigger function, given the audited table's primary key column
-- as its argument. Two groups of tables are audited:
--
--   business rules — segment, segment_rule, campaign, experiment
--   catalogs       — category, product, store, customer, app_user
--
-- Individual sales are deliberately not audited: their volume and history
-- already live in transaction and transaction_line, and auditing them would
-- double the write cost of the busiest table in the model.
-- =========================================================

CREATE OR REPLACE FUNCTION fn_audit() RETURNS TRIGGER AS $$
DECLARE
    before_data jsonb := CASE WHEN TG_OP IN ('UPDATE','DELETE') THEN to_jsonb(OLD) END;
    after_data  jsonb := CASE WHEN TG_OP IN ('UPDATE','INSERT') THEN to_jsonb(NEW) END;
BEGIN
    -- A password hash is a credential, not an audit fact. Auditing app_user is
    -- what makes user management traceable; copying the hash into a second,
    -- longer-lived table would only widen where credentials can leak from.
    before_data := before_data - 'password_hash';
    after_data  := after_data  - 'password_hash';

    -- The application sets mosaiq.user_id on the connection once F4-01 knows
    -- who is logged in. Until then, and for anything the seed or a maintenance
    -- script does, the actor is genuinely unknown and the column stays NULL
    -- rather than being filled with a fiction.
    INSERT INTO audit_log (user_id, entity, entity_pk, action, data_before, data_after)
    VALUES (
        NULLIF(current_setting('mosaiq.user_id', true), '')::uuid,
        TG_TABLE_NAME,
        COALESCE(after_data ->> TG_ARGV[0], before_data ->> TG_ARGV[0]),
        TG_OP,
        before_data,
        after_data
    );
    RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql
-- Runs as definer_audit, which may only append to audit_log, so an audited
-- write is recorded whoever makes it: retail_app, or a svc_* role that holds
-- no privilege on audit_log at all (ADR-0034, E-12). mosaiq.user_id is a
-- session setting, so the actor still comes from the caller's connection.
SECURITY DEFINER
SET search_path = public, pg_temp;

ALTER FUNCTION fn_audit() OWNER TO definer_audit;
REVOKE EXECUTE ON FUNCTION fn_audit() FROM PUBLIC;

CREATE TRIGGER trg_audit_segment
AFTER INSERT OR UPDATE OR DELETE ON segment
FOR EACH ROW EXECUTE FUNCTION fn_audit('segment_id');

CREATE TRIGGER trg_audit_segment_rule
AFTER INSERT OR UPDATE OR DELETE ON segment_rule
FOR EACH ROW EXECUTE FUNCTION fn_audit('rule_id');

CREATE TRIGGER trg_audit_campaign
AFTER INSERT OR UPDATE OR DELETE ON campaign
FOR EACH ROW EXECUTE FUNCTION fn_audit('campaign_id');

CREATE TRIGGER trg_audit_experiment
AFTER INSERT OR UPDATE OR DELETE ON experiment
FOR EACH ROW EXECUTE FUNCTION fn_audit('experiment_id');

CREATE TRIGGER trg_audit_category
AFTER INSERT OR UPDATE OR DELETE ON category
FOR EACH ROW EXECUTE FUNCTION fn_audit('category_id');

CREATE TRIGGER trg_audit_product
AFTER INSERT OR UPDATE OR DELETE ON product
FOR EACH ROW EXECUTE FUNCTION fn_audit('product_id');

CREATE TRIGGER trg_audit_store
AFTER INSERT OR UPDATE OR DELETE ON store
FOR EACH ROW EXECUTE FUNCTION fn_audit('store_id');

CREATE TRIGGER trg_audit_customer
AFTER INSERT OR UPDATE OR DELETE ON customer
FOR EACH ROW EXECUTE FUNCTION fn_audit('customer_id');

CREATE TRIGGER trg_audit_app_user
AFTER INSERT OR UPDATE OR DELETE ON app_user
FOR EACH ROW EXECUTE FUNCTION fn_audit('user_id');

-- channel and role are edited from /admin/channels and /admin/roles like the
-- catalogs above, so they are audited for the same reason. role carries the
-- code the permission matrix keys on (web/middleware/authz.py): without this,
-- a role could be created, renamed or dropped on the instance with nothing
-- recording who did it.
CREATE TRIGGER trg_audit_channel
AFTER INSERT OR UPDATE OR DELETE ON channel
FOR EACH ROW EXECUTE FUNCTION fn_audit('channel_id');

CREATE TRIGGER trg_audit_role
AFTER INSERT OR UPDATE OR DELETE ON role
FOR EACH ROW EXECUTE FUNCTION fn_audit('role_id');

-- ADR-0017 retires the customer's mutable current-segment column in favour of
-- durable history; this trigger is what replaces the audit signal that used
-- to come from updating that column. Every open/close pair a segmentation run
-- writes (web/db/segments.py) is now traceable the same way a catalog edit is.
CREATE TRIGGER trg_audit_customer_segment_history
AFTER INSERT OR UPDATE OR DELETE ON customer_segment_history
FOR EACH ROW EXECUTE FUNCTION fn_audit('history_id');

-- =========================================================
-- BOUNDARY SECTIONS (ADR-0034, ADR-0040, ADR-0044, ADR-0046, F13-07)
--
-- Everything above is the legacy model and stays as it is. Everything a
-- service boundary adds lives below, one section per boundary:
--
--   -- BEGIN BOUNDARY <name>
--   ...
--   -- END BOUNDARY <name>
--
-- <name> is a service name from contracts/registry.yaml. Sections sit after
-- the legacy sections and before APPLICATION ROLE VERIFICATION, which stays
-- last. Inside a section the order is: CREATE SCHEMA, tables, api_<suffix>
-- objects, triggers, grants.
--
-- A section is applied by hand to the live database before the service that
-- needs it deploys, and CI re-applies every section after the full run, so
-- each one must be re-runnable:
--   - IF NOT EXISTS for schemas, tables and indexes;
--   - CREATE OR REPLACE for views, functions and triggers;
--   - a constraint only inside a DO block that checks pg_constraint first;
--   - GRANT, REVOKE and ALTER ... OWNER, which re-run as they are.
-- CI also lints the markers: every name registered, every BEGIN with its END,
-- and no section after the verification block.
--
-- Naming (ADR-0046): a boundary's contract objects go in api_<suffix>, its
-- outbox in <suffix>_events, and its own tables in a schema named for it,
-- <suffix> being its svc_ role suffix. Only legacy tables live in public.
-- =========================================================

-- BEGIN OUTBOX TEMPLATE
-- The one copy of the outbox and capture pattern (ADR-0044). A boundary that
-- publishes a stream copies this into its own section, replacing <suffix>
-- (its svc_ role suffix), <table> (the schema-qualified captured table),
-- <entity_column> and <event>. tests/test_outbox_template.py renders it from
-- here, so this block must stay valid SQL once the leading "-- " is removed.
--
-- CREATE SCHEMA IF NOT EXISTS <suffix>_events;
-- REVOKE ALL ON SCHEMA <suffix>_events FROM PUBLIC;
--
-- -- Keys only, never state: consumers re-read the producer's api_ view.
-- CREATE TABLE IF NOT EXISTS <suffix>_events.outbox (
--     event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
--     stream      text NOT NULL,
--     event_type  text NOT NULL,
--     entity_id   text NOT NULL,
--     occurred_at timestamptz NOT NULL DEFAULT now(),
--     relayed_at  timestamptz
-- );
-- CREATE INDEX IF NOT EXISTS outbox_unrelayed
--     ON <suffix>_events.outbox (occurred_at) WHERE relayed_at IS NULL;
--
-- -- Captures every writer, the web included, in the writer's transaction.
-- CREATE OR REPLACE FUNCTION <suffix>_events.capture_<event>() RETURNS trigger
-- LANGUAGE plpgsql
-- SECURITY DEFINER
-- SET search_path = pg_catalog, pg_temp
-- AS $capture$
-- DECLARE
--     entity text;
-- BEGIN
--     IF TG_OP = 'DELETE' THEN
--         entity := OLD.<entity_column>::text;
--     ELSE
--         entity := NEW.<entity_column>::text;
--     END IF;
--     INSERT INTO <suffix>_events.outbox (stream, event_type, entity_id)
--     VALUES ('stream:<suffix>.v1.<event>', TG_OP, entity);
--     -- An empty wake-up for the owner's relay; only that relay LISTENs.
--     PERFORM pg_notify('<suffix>_outbox', '');
--     RETURN NULL;
-- END
-- $capture$;
-- ALTER FUNCTION <suffix>_events.capture_<event>() OWNER TO definer_<suffix>;
-- REVOKE EXECUTE ON FUNCTION <suffix>_events.capture_<event>() FROM PUBLIC;
--
-- CREATE OR REPLACE TRIGGER trg_capture_<event>
-- AFTER INSERT OR UPDATE OR DELETE ON <table>
-- FOR EACH ROW EXECUTE FUNCTION <suffix>_events.capture_<event>();
--
-- GRANT USAGE ON SCHEMA <suffix>_events TO svc_<suffix>, definer_<suffix>;
-- GRANT INSERT ON <suffix>_events.outbox TO definer_<suffix>;
-- GRANT SELECT, DELETE, UPDATE (relayed_at) ON <suffix>_events.outbox TO svc_<suffix>;
--
-- -- And in sql/seed-exempt.txt, with its reason above it:
-- --   <suffix>_events.outbox
-- END OUTBOX TEMPLATE

-- BEGIN BOUNDARY sales
-- The `svc-sales:` namespace of transaction.source_transaction_id
-- (ADR-0039). Only svc_sales may write into it, and svc_sales may write
-- nothing else, so a CSV row can never collide with or append to a phone
-- sale. Both guards are SECURITY INVOKER: they read session_user, the role
-- that connected, which SET ROLE cannot change. SQLSTATE 22023 is a DataError
-- that ingest_row already reports as a malformed row, so the web is unchanged.
-- The transaction guard also requires data_origin = 'OBSERVED' from svc_sales
-- (ADR-0052, #559): a phone sale is never seeded, and a 22023 from that
-- check is a sales-service defect. sales-service's own story (F14-18) extends
-- this section and does not re-create these guards.

CREATE OR REPLACE FUNCTION fn_sales_namespace_guard_transaction() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF (NEW.source_transaction_id LIKE 'svc-sales:%')
       IS DISTINCT FROM (session_user = 'svc_sales') THEN
        RAISE EXCEPTION 'source_transaction_id is outside the namespace of %', session_user
            USING ERRCODE = '22023';
    END IF;
    IF session_user = 'svc_sales' AND NEW.data_origin IS DISTINCT FROM 'OBSERVED' THEN
        RAISE EXCEPTION 'svc_sales records OBSERVED sales only (ADR-0052)'
            USING ERRCODE = '22023';
    END IF;
    -- ADR-0070 section 5. A file imported through sales-service writes the id
    -- `svc-sales:imp:<id>`, the web writes `<id>`. Neither path may record the
    -- other's sale again, so each refuses the id when the other's exists. Both
    -- take the same advisory lock first (key 293001, contracts/db/
    -- advisory-locks.md), so two concurrent loads of one file cannot both pass.
    IF session_user = 'svc_sales' AND NEW.source_transaction_id LIKE 'svc-sales:imp:%' THEN
        PERFORM pg_advisory_xact_lock(293001, hashtext(substr(NEW.source_transaction_id, 15)));
        IF EXISTS (SELECT FROM public.transaction AS t
                    WHERE t.source_transaction_id = substr(NEW.source_transaction_id, 15)) THEN
            RAISE EXCEPTION 'duplicate: % was already loaded through the other path',
                substr(NEW.source_transaction_id, 15)
                USING ERRCODE = '22023';
        END IF;
    ELSIF session_user <> 'svc_sales' THEN
        PERFORM pg_advisory_xact_lock(293001, hashtext(NEW.source_transaction_id));
        IF EXISTS (SELECT FROM public.transaction AS t
                    WHERE t.source_transaction_id = 'svc-sales:imp:' || NEW.source_transaction_id) THEN
            RAISE EXCEPTION 'duplicate: % was already loaded through the other path',
                NEW.source_transaction_id
                USING ERRCODE = '22023';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION fn_sales_namespace_guard_line() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF session_user <> 'svc_sales' AND EXISTS (
        SELECT FROM public.transaction AS t
        WHERE t.transaction_id = NEW.transaction_id
          AND t.source_transaction_id LIKE 'svc-sales:%'
    ) THEN
        RAISE EXCEPTION 'transaction % belongs to the svc-sales: namespace', NEW.transaction_id
            USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER trg_sales_namespace_guard
BEFORE INSERT ON public.transaction
FOR EACH ROW EXECUTE FUNCTION fn_sales_namespace_guard_transaction();

CREATE OR REPLACE TRIGGER trg_sales_namespace_guard
BEFORE INSERT ON public.transaction_line
FOR EACH ROW EXECUTE FUNCTION fn_sales_namespace_guard_line();

-- sales-service (F14-18, ADR-0039). Its provenance table, its outbox for
-- stream:sales.v1.recorded, its contract view and the grants of svc_sales.
CREATE SCHEMA IF NOT EXISTS sales;
REVOKE ALL ON SCHEMA sales FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS api_sales;
REVOKE ALL ON SCHEMA api_sales FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS sales_events;
REVOKE ALL ON SCHEMA sales_events FROM PUBLIC;

-- One row per phone sale, written in the sale's own transaction and never
-- changed. The Idempotency-Key is not stored: it is the suffix of the header's
-- source_transaction_id (ADR-0039). request_sha256 is the canonical request
-- hash a retry is compared with.
CREATE TABLE IF NOT EXISTS sales.sale_capture (
    transaction_id bigint PRIMARY KEY
        REFERENCES public.transaction (transaction_id) ON DELETE CASCADE,
    request_sha256 bytea NOT NULL
        CONSTRAINT sale_capture_request_sha256_check
        CHECK (octet_length(request_sha256) = 32),
    recorded_by    uuid NOT NULL
        REFERENCES public.app_user (user_id) ON DELETE RESTRICT,
    captured_at    timestamptz NOT NULL DEFAULT now()
);

-- Keys only, never state: consumers re-read api_sales.purchase_line_v1.
CREATE TABLE IF NOT EXISTS sales_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON sales_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- contracts/db/api_sales.purchase_line.v1.md: one row per sale line, phone and
-- CSV alike. Definer rights, so a consumer needs no privilege on either table.
CREATE OR REPLACE VIEW api_sales.purchase_line_v1
WITH (security_invoker = off) AS
SELECT t.transaction_id, t.customer_id, t.store_id, t.channel_id, t.occurred_at,
       l.product_id, l.quantity, l.unit_price
FROM public.transaction AS t
JOIN public.transaction_line AS l ON l.transaction_id = t.transaction_id;
REVOKE ALL ON api_sales.purchase_line_v1 FROM PUBLIC;

-- stream:sales.v1.recorded, for every writer of a header, the web's CSV path
-- included. A service sale fires once (its total is never updated); a CSV row
-- that creates a header fires twice (the insert, then recompute_total), and a
-- line appended to an existing header fires once more (ADR-0039).
CREATE OR REPLACE FUNCTION sales_events.capture_recorded() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
BEGIN
    INSERT INTO sales_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:sales.v1.recorded', TG_OP, NEW.transaction_id::text);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('sales_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION sales_events.capture_recorded() OWNER TO definer_sales;
REVOKE EXECUTE ON FUNCTION sales_events.capture_recorded() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_recorded
AFTER INSERT OR UPDATE OF total ON public.transaction
FOR EACH ROW EXECUTE FUNCTION sales_events.capture_recorded();

-- svc_sales records sales and never changes or deletes one. It reaches
-- inventory only through api_stock.consume_v1 (granted in BOUNDARY stock), and
-- customers and products only through their api_ views. Each is a row of
-- contracts/db/grants.csv.
GRANT SELECT, INSERT ON public.transaction TO svc_sales;
GRANT SELECT, INSERT ON public.transaction_line TO svc_sales;
GRANT SELECT ON public.channel TO svc_sales;
GRANT USAGE ON SCHEMA sales TO svc_sales;
GRANT SELECT, INSERT ON sales.sale_capture TO svc_sales;

GRANT USAGE ON SCHEMA sales_events TO svc_sales, definer_sales;
GRANT INSERT ON sales_events.outbox TO definer_sales;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON sales_events.outbox TO svc_sales;

GRANT USAGE ON SCHEMA api_sales TO svc_sales, svc_recommendations;
GRANT SELECT ON api_sales.purchase_line_v1 TO svc_sales, svc_recommendations;

-- Reversals (F21-01 ADR-0062, F21-03). A cancellation or a return is an
-- append-only fact recorded beside the sale; the sale, its lines' quantity and
-- its total are never edited. docs/data-model.md holds the 4NF justification.
--
-- transaction_line.returned_quantity is the running total of a line's reversed
-- units. It repeats what the reversal lines add up to, as inventory repeats its
-- movements, so every reader of net units (public.net_sale_line, below) reads
-- one column instead of summing a table the web cannot reach (ADR-0046). Three
-- guards keep the copy honest: the CHECK, the writer guard and the deferred
-- equality trigger.
ALTER TABLE public.transaction_line
    ADD COLUMN IF NOT EXISTS returned_quantity integer NOT NULL DEFAULT 0;
DO $returned_quantity$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_constraint
                    WHERE conrelid = 'public.transaction_line'::regclass
                      AND conname = 'transaction_line_returned_quantity_check') THEN
        ALTER TABLE public.transaction_line
            ADD CONSTRAINT transaction_line_returned_quantity_check
            CHECK (returned_quantity BETWEEN 0 AND quantity);
    END IF;
END
$returned_quantity$;

-- Only svc_sales writes returned_quantity, and a line is born with none
-- returned. session_user is the role that connected, which SET ROLE cannot
-- change, as in the namespace guards above. SQLSTATE 22023 is a DataError that
-- ingest_row reports as a malformed row, so the web is unchanged.
CREATE OR REPLACE FUNCTION fn_sales_returned_quantity_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.returned_quantity <> 0 THEN
            RAISE EXCEPTION 'a sale line is recorded with nothing returned'
                USING ERRCODE = '22023';
        END IF;
    ELSIF session_user <> 'svc_sales' THEN
        RAISE EXCEPTION 'only svc_sales writes returned_quantity (ADR-0062)'
            USING ERRCODE = '22023';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER trg_sales_returned_quantity_guard
BEFORE INSERT OR UPDATE OF returned_quantity ON public.transaction_line
FOR EACH ROW EXECUTE FUNCTION fn_sales_returned_quantity_guard();

-- One row per reversal. The idempotency key is this resource's own, kept here
-- because no other column holds it (a sale's key is the suffix of its
-- source_transaction_id). request_sha256 is the canonical request a retry is
-- compared with. The customer and the store are the sale's, never copied.
CREATE TABLE IF NOT EXISTS sales.sale_reversal (
    reversal_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    transaction_id  bigint NOT NULL
        REFERENCES public.transaction (transaction_id) ON DELETE CASCADE,
    kind            text NOT NULL
        CONSTRAINT sale_reversal_kind_check
        CHECK (kind IN ('CANCELLATION', 'RETURN')),
    reason          text
        CONSTRAINT sale_reversal_reason_check
        CHECK (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 200)),
    idempotency_key uuid NOT NULL
        CONSTRAINT sale_reversal_idempotency_key_key UNIQUE,
    request_sha256  bytea NOT NULL
        CONSTRAINT sale_reversal_request_sha256_check
        CHECK (octet_length(request_sha256) = 32),
    recorded_by     uuid NOT NULL
        REFERENCES public.app_user (user_id) ON DELETE RESTRICT,
    recorded_at     timestamptz NOT NULL DEFAULT now(),
    -- A cancellation states its reason on the header, a return on each line.
    CONSTRAINT sale_reversal_reason_kind_check
        CHECK ((kind = 'CANCELLATION') = (reason IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS sale_reversal_sale
    ON sales.sale_reversal (transaction_id);
CREATE INDEX IF NOT EXISTS sale_reversal_recorded
    ON sales.sale_reversal (recorded_at DESC, reversal_id DESC);
-- A sale is cancelled once: a second cancellation could only reverse nothing.
CREATE UNIQUE INDEX IF NOT EXISTS sale_reversal_one_cancellation
    ON sales.sale_reversal (transaction_id) WHERE kind = 'CANCELLATION';

-- The lines a reversal touched. (transaction_id, product_id) names the sold
-- line, so the units reversed can never name a line the sale does not have.
CREATE TABLE IF NOT EXISTS sales.sale_reversal_line (
    reversal_id    bigint NOT NULL
        REFERENCES sales.sale_reversal (reversal_id) ON DELETE CASCADE,
    transaction_id bigint NOT NULL,
    product_id     integer NOT NULL,
    quantity       integer NOT NULL
        CONSTRAINT sale_reversal_line_quantity_check CHECK (quantity > 0),
    reason         text
        CONSTRAINT sale_reversal_line_reason_check
        CHECK (reason IS NULL OR (btrim(reason) <> '' AND char_length(reason) <= 200)),
    resellable     boolean NOT NULL DEFAULT true,
    PRIMARY KEY (reversal_id, product_id),
    FOREIGN KEY (transaction_id, product_id)
        REFERENCES public.transaction_line (transaction_id, product_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS sale_reversal_line_sold
    ON sales.sale_reversal_line (transaction_id, product_id);

-- The sum of a sale line's reversals can never exceed what was sold, whoever
-- inserts (the service or a direct INSERT). The sold line is locked first, so
-- two reversals of one line queue instead of both seeing the same remainder.
-- A cancellation takes the whole line, and a return states a reason; both are
-- what the contract says and a direct INSERT must not slip past them.
CREATE OR REPLACE FUNCTION sales.fn_sale_reversal_line_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    header_sale bigint;
    header_kind text;
    sold        integer;
    already     integer;
BEGIN
    SELECT r.transaction_id, r.kind INTO header_sale, header_kind
      FROM sales.sale_reversal AS r WHERE r.reversal_id = NEW.reversal_id;
    IF header_sale IS DISTINCT FROM NEW.transaction_id THEN
        RAISE EXCEPTION 'reversal % is not a reversal of sale %', NEW.reversal_id, NEW.transaction_id
            USING ERRCODE = '23514', CONSTRAINT = 'sale_reversal_line_sale_check';
    END IF;
    SELECT l.quantity INTO sold
      FROM public.transaction_line AS l
     WHERE l.transaction_id = NEW.transaction_id AND l.product_id = NEW.product_id
       FOR UPDATE;
    SELECT coalesce(sum(x.quantity), 0) INTO already
      FROM sales.sale_reversal_line AS x
     WHERE x.transaction_id = NEW.transaction_id AND x.product_id = NEW.product_id;
    IF already + NEW.quantity > sold THEN
        RAISE EXCEPTION 'reversing % more of product % would exceed the % sold in sale % (% already reversed)',
            NEW.quantity, NEW.product_id, sold, NEW.transaction_id, already
            USING ERRCODE = '23514', CONSTRAINT = 'sale_reversal_line_within_sale';
    END IF;
    IF header_kind = 'CANCELLATION' AND NEW.quantity <> sold THEN
        RAISE EXCEPTION 'a cancellation reverses every unit of a line'
            USING ERRCODE = '23514', CONSTRAINT = 'sale_reversal_line_cancellation_whole';
    END IF;
    IF (header_kind = 'RETURN') = (NEW.reason IS NULL) THEN
        RAISE EXCEPTION 'a return line states a reason and a cancellation line does not'
            USING ERRCODE = '23514', CONSTRAINT = 'sale_reversal_line_reason_kind_check';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE TRIGGER trg_sale_reversal_line_guard
BEFORE INSERT ON sales.sale_reversal_line
FOR EACH ROW EXECUTE FUNCTION sales.fn_sale_reversal_line_guard();

-- At commit, a line's returned_quantity equals the sum of its reversal lines:
-- a reversal never lands without its effect, and the effect never without a
-- reversal (contracts/db/api_sales.net_purchase_line.v1.md). Deferred, since
-- the service writes the reversal line first and the column after it.
CREATE OR REPLACE FUNCTION sales.fn_returned_quantity_matches() RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, pg_temp
AS $$
DECLARE
    expected integer;
    actual   integer;
BEGIN
    SELECT l.returned_quantity INTO actual
      FROM public.transaction_line AS l
     WHERE l.transaction_id = NEW.transaction_id AND l.product_id = NEW.product_id;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT coalesce(sum(x.quantity), 0) INTO expected
      FROM sales.sale_reversal_line AS x
     WHERE x.transaction_id = NEW.transaction_id AND x.product_id = NEW.product_id;
    IF actual <> expected THEN
        RAISE EXCEPTION 'returned_quantity % of sale % product % differs from its reversals (%)',
            actual, NEW.transaction_id, NEW.product_id, expected
            USING ERRCODE = '23514', CONSTRAINT = 'returned_quantity_matches_reversals';
    END IF;
    RETURN NULL;
END
$$;

-- A constraint trigger cannot be replaced in place, so it is dropped first.
DROP TRIGGER IF EXISTS trg_returned_quantity_matches ON sales.sale_reversal_line;
CREATE CONSTRAINT TRIGGER trg_returned_quantity_matches
AFTER INSERT ON sales.sale_reversal_line
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sales.fn_returned_quantity_matches();

DROP TRIGGER IF EXISTS trg_returned_quantity_matches ON public.transaction_line;
CREATE CONSTRAINT TRIGGER trg_returned_quantity_matches
AFTER UPDATE OF returned_quantity ON public.transaction_line
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION sales.fn_returned_quantity_matches();

-- stream:sales.v1.reversed (contracts/streams/sales.reversed.v1.md): exactly
-- one outbox row per reversal, in the reversal's own transaction, so a
-- rollback publishes nothing. The entity is `sale_id:reversal_id`.
CREATE OR REPLACE FUNCTION sales_events.capture_reversed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
BEGIN
    INSERT INTO sales_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:sales.v1.reversed', TG_OP,
            NEW.transaction_id::text || ':' || NEW.reversal_id::text);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('sales_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION sales_events.capture_reversed() OWNER TO definer_sales;
REVOKE EXECUTE ON FUNCTION sales_events.capture_reversed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_reversed
AFTER INSERT ON sales.sale_reversal
FOR EACH ROW EXECUTE FUNCTION sales_events.capture_reversed();

-- The one definition of net units (ADR-0062). public.net_sale_line holds a row
-- for every sale line with at least one unit left, and public.net_sale one row
-- for every sale with at least one such line, whose net amount is therefore
-- above zero: a sale whose net amount is zero stops counting as a purchase.
-- The web reads these two (retail_app reaches public alone, ADR-0046);
-- api_sales.net_purchase_line_v1 publishes the same definition to services.
-- transaction.total stays what was charged and is exposed as `total`.
CREATE OR REPLACE VIEW public.net_sale_line AS
SELECT t.transaction_id, t.customer_id, t.store_id, t.channel_id, t.occurred_at,
       t.data_origin, l.product_id, l.quantity, l.returned_quantity,
       l.quantity - l.returned_quantity AS net_quantity,
       l.unit_price,
       ((l.quantity - l.returned_quantity) * l.unit_price)::numeric(12,2) AS net_amount
FROM public.transaction AS t
JOIN public.transaction_line AS l ON l.transaction_id = t.transaction_id
WHERE l.quantity > l.returned_quantity;

CREATE OR REPLACE VIEW public.net_sale AS
SELECT t.transaction_id, t.source_transaction_id, t.customer_id, t.store_id,
       t.channel_id, t.occurred_at, t.data_origin, t.total,
       n.net_total, n.net_units
FROM public.transaction AS t
CROSS JOIN LATERAL (
    SELECT sum(l.net_amount)::numeric(12,2) AS net_total,
           sum(l.net_quantity)::bigint AS net_units
    FROM public.net_sale_line AS l
    WHERE l.transaction_id = t.transaction_id
) AS n
WHERE n.net_units > 0;

REVOKE ALL ON public.net_sale_line, public.net_sale FROM PUBLIC;
REVOKE INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER
    ON public.net_sale_line, public.net_sale FROM retail_app;
GRANT SELECT ON public.net_sale_line, public.net_sale TO retail_app;

-- contracts/db/api_sales.net_purchase_line.v1.md: the lines that still count,
-- phone and CSV alike. Definer rights, like purchase_line_v1.
CREATE OR REPLACE VIEW api_sales.net_purchase_line_v1
WITH (security_invoker = off) AS
SELECT transaction_id, customer_id, store_id, channel_id, occurred_at,
       product_id, quantity, returned_quantity, net_quantity, unit_price, net_amount
FROM public.net_sale_line;
REVOKE ALL ON api_sales.net_purchase_line_v1 FROM PUBLIC;

-- svc_sales reaches inventory only through api_stock.restock_v1 (granted in
-- BOUNDARY stock), and writes returned_quantity and nothing else of a line.
GRANT UPDATE (returned_quantity) ON public.transaction_line TO svc_sales;
GRANT SELECT, INSERT ON sales.sale_reversal, sales.sale_reversal_line TO svc_sales;
GRANT SELECT ON api_sales.net_purchase_line_v1 TO svc_sales, svc_recommendations;

-- Sales imports (F26-06 ADR-0070, F26-08). A file reaches sales-service as
-- numbered chunks of rows, is staged here and applied one row at a time by the
-- worker, a second process of the same image. docs/data-model.md holds the 4NF
-- justification.
--
-- One row per import. The Idempotency-Key is this resource's own and is kept
-- here because no other column holds it. request_sha256 is the canonical
-- request a retry is compared with. last_chunk_at is when a chunk last arrived,
-- which is what the 24-hour expiry counts from.
CREATE TABLE IF NOT EXISTS sales.import (
    import_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key uuid NOT NULL
        CONSTRAINT import_idempotency_key_key UNIQUE,
    request_sha256  bytea NOT NULL
        CONSTRAINT import_request_sha256_check CHECK (octet_length(request_sha256) = 32),
    contract_version smallint NOT NULL
        CONSTRAINT import_contract_version_check CHECK (contract_version = 1),
    total_rows      integer NOT NULL
        CONSTRAINT import_total_rows_check CHECK (total_rows BETWEEN 1 AND 100000),
    chunk_count     integer NOT NULL
        CONSTRAINT import_chunk_count_check CHECK (chunk_count BETWEEN 1 AND 667),
    status          text NOT NULL DEFAULT 'RECEIVING'
        CONSTRAINT import_status_check
        CHECK (status IN ('RECEIVING', 'PROCESSING', 'COMPLETED', 'CANCELLED', 'EXPIRED')),
    created_by      uuid NOT NULL
        REFERENCES public.app_user (user_id) ON DELETE RESTRICT,
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_chunk_at   timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    CONSTRAINT import_finished_check
        CHECK ((status IN ('COMPLETED', 'CANCELLED', 'EXPIRED')) = (finished_at IS NOT NULL))
);
-- One import is open at a time (ADR-0070 section 2): the database says so too.
CREATE UNIQUE INDEX IF NOT EXISTS import_one_open
    ON sales.import ((true)) WHERE status IN ('RECEIVING', 'PROCESSING');
CREATE INDEX IF NOT EXISTS import_created
    ON sales.import (created_at DESC, import_id DESC);

-- One row per staged chunk. `rows` is the payload the worker applies and is
-- nulled as soon as the chunk is done, cancelled or expired: a staged copy of
-- customer ids does not outlive the work. row_count stays, so the counts of an
-- import that never finished still reconcile.
CREATE TABLE IF NOT EXISTS sales.import_chunk (
    import_id      bigint NOT NULL
        REFERENCES sales.import (import_id) ON DELETE CASCADE,
    sequence       integer NOT NULL
        CONSTRAINT import_chunk_sequence_check CHECK (sequence BETWEEN 0 AND 666),
    request_sha256 bytea NOT NULL
        CONSTRAINT import_chunk_request_sha256_check CHECK (octet_length(request_sha256) = 32),
    row_count      integer NOT NULL
        CONSTRAINT import_chunk_row_count_check CHECK (row_count BETWEEN 1 AND 150),
    first_line     integer NOT NULL
        CONSTRAINT import_chunk_first_line_check CHECK (first_line >= 1),
    last_line      integer NOT NULL,
    rows           jsonb,
    status         text NOT NULL DEFAULT 'PENDING'
        CONSTRAINT import_chunk_status_check CHECK (status IN ('PENDING', 'DONE')),
    staged_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (import_id, sequence),
    CONSTRAINT import_chunk_lines_check CHECK (last_line >= first_line),
    CONSTRAINT import_chunk_done_check CHECK (status <> 'DONE' OR rows IS NULL)
);
CREATE INDEX IF NOT EXISTS import_chunk_pending
    ON sales.import_chunk (import_id, sequence) WHERE status = 'PENDING';

-- One row per applied or refused line of the file. It is the durable record
-- that makes a resume exact: the outcome is written in the same transaction as
-- the sale line it describes, so a line is never applied twice. An accepted
-- outcome points at the sale line it made (transaction_id, product_id), which
-- is how a later duplicate names the first line; a refused one carries what the
-- report shows. The row's cells are not kept.
CREATE TABLE IF NOT EXISTS sales.import_row_outcome (
    import_id      bigint NOT NULL
        REFERENCES sales.import (import_id) ON DELETE CASCADE,
    line           integer NOT NULL
        CONSTRAINT import_row_outcome_line_check CHECK (line >= 2),
    outcome        text NOT NULL
        CONSTRAINT import_row_outcome_outcome_check CHECK (outcome IN ('ACCEPTED', 'REJECTED')),
    code           text,
    field          text,
    message        text,
    duplicate_of   text
        CONSTRAINT import_row_outcome_duplicate_of_check
        CHECK (duplicate_of IN ('FILE', 'RECORDED_SALE')),
    first_line     integer,
    sale_id        bigint,
    transaction_id bigint,
    product_id     integer,
    PRIMARY KEY (import_id, line),
    FOREIGN KEY (transaction_id, product_id)
        REFERENCES public.transaction_line (transaction_id, product_id) ON DELETE SET NULL,
    CONSTRAINT import_row_outcome_shape_check CHECK (
        CASE outcome
            WHEN 'ACCEPTED' THEN code IS NULL AND field IS NULL AND message IS NULL
                AND duplicate_of IS NULL AND first_line IS NULL AND sale_id IS NULL
            ELSE code IS NOT NULL AND message IS NOT NULL
                AND transaction_id IS NULL AND product_id IS NULL
                AND ((code = 'duplicate_row') = (duplicate_of IS NOT NULL))
                AND (first_line IS NULL OR duplicate_of = 'FILE')
                AND (sale_id IS NULL OR duplicate_of = 'RECORDED_SALE')
        END)
);
CREATE INDEX IF NOT EXISTS import_row_outcome_rejected
    ON sales.import_row_outcome (import_id, line) WHERE outcome = 'REJECTED';
CREATE INDEX IF NOT EXISTS import_row_outcome_line_made
    ON sales.import_row_outcome (import_id, transaction_id, product_id)
    WHERE outcome = 'ACCEPTED';

-- A header an import made has a capture row like a phone sale's, so ADR-0039's
-- query (one sales.sale_capture row for every `svc-sales:` header) still
-- returns nothing. import_id says which import made it.
ALTER TABLE sales.sale_capture
    ADD COLUMN IF NOT EXISTS import_id bigint REFERENCES sales.import (import_id);
CREATE INDEX IF NOT EXISTS sale_capture_import
    ON sales.sale_capture (import_id) WHERE import_id IS NOT NULL;

-- An import appends lines to a header and recomputes its total, as the web's
-- CSV path does (the deferred trigger on both tables holds total equal to the
-- sum of the lines). That is the one column of a header svc_sales may change.
GRANT UPDATE (total) ON public.transaction TO svc_sales;
GRANT SELECT, INSERT, UPDATE ON sales.import, sales.import_chunk TO svc_sales;
GRANT SELECT, INSERT ON sales.import_row_outcome TO svc_sales;
-- END BOUNDARY sales

-- BEGIN BOUNDARY products
-- products-service (F14-06, ADR-0034, ADR-0044). Its contract view, its
-- outbox for stream:products.v1.changed and the grants of svc_products. The
-- capture trigger fires for every writer of public.product, the frozen web
-- and a hard delete included, so no change is invisible to the cache
-- invalidation of products and of recommendations.
--
-- F21-10 (#790) adds the product's commercial details in the service schema
-- `products` (ADR-0046): a commercial status, attributes, a barcode and
-- directed relations, each in its own table keyed by product_id and none a
-- column of public.product, which stays as the web knows it. The status and
-- public.product.is_active are kept in step by two triggers, so neither the
-- web nor the service can leave them disagreeing (see the triggers below).
-- docs/data-model.md §2.4 holds the 4NF justification.

CREATE SCHEMA IF NOT EXISTS api_products;
REVOKE ALL ON SCHEMA api_products FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS products_events;
REVOKE ALL ON SCHEMA products_events FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS products;
REVOKE ALL ON SCHEMA products FROM PUBLIC;

-- Keys only, never state: consumers re-read the producer's api_ view.
CREATE TABLE IF NOT EXISTS products_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON products_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- GTIN check digit (GS1): from the right of the body, the digits weigh 3, 1,
-- 3, 1, ...; the check digit completes their sum to a multiple of ten. Pure and
-- immutable, so a CHECK can use it. products-service validates the same rule
-- before it writes, to answer 422 invalid_barcode; this is the backstop, and
-- services/products/tests pins the two together.
CREATE OR REPLACE FUNCTION products.gtin_check_digit(body text) RETURNS integer
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $gtin$
    SELECT (10 - coalesce(sum(substr(body, length(body) - i + 1, 1)::integer
                              * CASE WHEN i % 2 = 1 THEN 3 ELSE 1 END), 0) % 10) % 10
      FROM generate_series(1, length(body)) AS i
$gtin$;

CREATE OR REPLACE FUNCTION products.gtin_is_valid(code text) RETURNS boolean
LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE
SET search_path = pg_catalog, pg_temp
AS $gtin$
    SELECT CASE
        WHEN code ~ '^([0-9]{8}|[0-9]{12,14})$'
        THEN right(code, 1)::integer = products.gtin_check_digit(left(code, -1))
        ELSE false
    END
$gtin$;

-- One row per product that has an explicit commercial status. A product with
-- none reads as active when public.product.is_active, otherwise discontinued.
-- is_active is true exactly when the status is active (decision 2 of the
-- products contract, 1.2.0); paused and discontinued are both off sale.
CREATE TABLE IF NOT EXISTS products.product_status (
    product_id        integer NOT NULL,
    commercial_status text    NOT NULL,
    CONSTRAINT product_status_pkey PRIMARY KEY (product_id),
    CONSTRAINT product_status_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES public.product (product_id) ON DELETE CASCADE,
    CONSTRAINT product_status_commercial_status_check
        CHECK (commercial_status IN ('active', 'paused', 'discontinued'))
);

-- One row per (product, attribute name): the product's variable set of
-- name and value pairs, split out so that no relation carries two independent
-- multivalued facts (docs/data-model.md §2.4).
CREATE TABLE IF NOT EXISTS products.product_attribute (
    product_id integer NOT NULL,
    name       text    NOT NULL,
    value      text    NOT NULL,
    CONSTRAINT product_attribute_pkey PRIMARY KEY (product_id, name),
    CONSTRAINT product_attribute_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES public.product (product_id) ON DELETE CASCADE,
    CONSTRAINT product_attribute_name_check CHECK (name ~ '^[a-z][a-z0-9_]{0,39}$'),
    CONSTRAINT product_attribute_value_check CHECK (char_length(value) BETWEEN 1 AND 120)
);

-- At most one barcode per product, and one product per barcode: a GTIN-8, -12,
-- -13 or -14 with a valid check digit.
CREATE TABLE IF NOT EXISTS products.product_barcode (
    product_id integer NOT NULL,
    barcode    text    NOT NULL,
    CONSTRAINT product_barcode_pkey PRIMARY KEY (product_id),
    CONSTRAINT product_barcode_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES public.product (product_id) ON DELETE CASCADE,
    CONSTRAINT product_barcode_barcode_key UNIQUE (barcode),
    CONSTRAINT product_barcode_gtin_check CHECK (products.gtin_is_valid(barcode))
);

-- A directed relation: (A, B, substitute) does not imply (B, A, substitute).
-- Both ends are products; a product is never related to itself.
CREATE TABLE IF NOT EXISTS products.product_relation (
    product_id         integer NOT NULL,
    related_product_id integer NOT NULL,
    relation_kind      text    NOT NULL,
    CONSTRAINT product_relation_pkey
        PRIMARY KEY (product_id, relation_kind, related_product_id),
    CONSTRAINT product_relation_product_id_fkey
        FOREIGN KEY (product_id) REFERENCES public.product (product_id) ON DELETE CASCADE,
    CONSTRAINT product_relation_related_product_id_fkey
        FOREIGN KEY (related_product_id) REFERENCES public.product (product_id) ON DELETE CASCADE,
    CONSTRAINT product_relation_kind_check
        CHECK (relation_kind IN ('substitute', 'complement')),
    CONSTRAINT product_relation_not_self_check CHECK (product_id <> related_product_id)
);
-- A product delete (the web's) cascades through related_product_id, which the
-- primary key does not lead with.
CREATE INDEX IF NOT EXISTS idx_product_relation_related
    ON products.product_relation (related_product_id);

-- contracts/db/api_products.product_ref.v1.md. Definer rights
-- (security_invoker = off): a consumer reads these columns and nothing else of
-- public.product. image_path stays web-managed and is not published.
CREATE OR REPLACE VIEW api_products.product_ref_v1
WITH (security_invoker = off) AS
SELECT product_id, sku, name, category_id, list_price, is_active
FROM public.product;

-- contracts/db/api_products.product_relation.v1.md. Definer rights, so
-- recommendations reads the three columns without any privilege on the table.
CREATE OR REPLACE VIEW api_products.product_relation_v1
WITH (security_invoker = off) AS
SELECT product_id, related_product_id, relation_kind
FROM products.product_relation;

-- Captures every writer, the web included, in the writer's transaction.
CREATE OR REPLACE FUNCTION products_events.capture_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        entity := OLD.product_id::text;
    ELSE
        entity := NEW.product_id::text;
    END IF;
    INSERT INTO products_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:products.v1.changed', TG_OP, entity);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('products_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION products_events.capture_changed() OWNER TO definer_products;
REVOKE EXECUTE ON FUNCTION products_events.capture_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_changed
AFTER INSERT OR UPDATE OR DELETE ON public.product
FOR EACH ROW EXECUTE FUNCTION products_events.capture_changed();

-- The commercial details are product source of truth too: the same capture
-- function announces a change to any of them with the product's id, so the
-- caches of products and recommendations are invalidated the same way.
CREATE OR REPLACE TRIGGER trg_capture_status_changed
AFTER INSERT OR UPDATE OR DELETE ON products.product_status
FOR EACH ROW EXECUTE FUNCTION products_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_attribute_changed
AFTER INSERT OR UPDATE OR DELETE ON products.product_attribute
FOR EACH ROW EXECUTE FUNCTION products_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_barcode_changed
AFTER INSERT OR UPDATE OR DELETE ON products.product_barcode
FOR EACH ROW EXECUTE FUNCTION products_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_relation_changed
AFTER INSERT OR UPDATE OR DELETE ON products.product_relation
FOR EACH ROW EXECUTE FUNCTION products_events.capture_changed();

-- Catalog data is audited (RN-28), the actor coming from mosaiq.user_id, which
-- the kit sets in every service transaction. The entity is the table's name,
-- unique across schemas (ADR-0046), and the key is the product the row is
-- about; the before and after documents carry the rest.
CREATE OR REPLACE TRIGGER trg_audit_product_status
AFTER INSERT OR UPDATE OR DELETE ON products.product_status
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('product_id');
CREATE OR REPLACE TRIGGER trg_audit_product_attribute
AFTER INSERT OR UPDATE OR DELETE ON products.product_attribute
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('product_id');
CREATE OR REPLACE TRIGGER trg_audit_product_barcode
AFTER INSERT OR UPDATE OR DELETE ON products.product_barcode
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('product_id');
CREATE OR REPLACE TRIGGER trg_audit_product_relation
AFTER INSERT OR UPDATE OR DELETE ON products.product_relation
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('product_id');

-- Status and is_active in step, in both directions, by the database. Each
-- trigger runs only when no other trigger is running (pg_trigger_depth() < 1),
-- so one change cannot call the other back; the write each makes is the whole
-- of the sync.
--
-- A change of public.product.is_active, by the web or by PATCH
-- /v1/products/{id}: true sets the status active; false sets it discontinued,
-- unless it is already paused. It runs as definer_products, because the web
-- has no privilege on the status table.
CREATE OR REPLACE FUNCTION products.sync_status_from_product() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $sync$
BEGIN
    INSERT INTO products.product_status AS s (product_id, commercial_status)
    VALUES (NEW.product_id, CASE WHEN NEW.is_active THEN 'active' ELSE 'discontinued' END)
    ON CONFLICT (product_id) DO UPDATE
       SET commercial_status = EXCLUDED.commercial_status
     WHERE s.commercial_status IS DISTINCT FROM EXCLUDED.commercial_status
       AND NOT (s.commercial_status = 'paused' AND NOT NEW.is_active);
    RETURN NULL;
END
$sync$;
ALTER FUNCTION products.sync_status_from_product() OWNER TO definer_products;
REVOKE EXECUTE ON FUNCTION products.sync_status_from_product() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_sync_status_from_product
AFTER UPDATE OF is_active ON public.product
FOR EACH ROW
WHEN (OLD.is_active IS DISTINCT FROM NEW.is_active AND pg_trigger_depth() < 1)
EXECUTE FUNCTION products.sync_status_from_product();

-- A write of the status (PATCH /v1/products/{id}/details): active sets
-- is_active true, paused and discontinued set it false. It runs with the
-- writer's own rights, and svc_products holds UPDATE (is_active) already.
CREATE OR REPLACE FUNCTION products.sync_product_from_status() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $sync$
BEGIN
    UPDATE public.product
       SET is_active = (NEW.commercial_status = 'active')
     WHERE product_id = NEW.product_id
       AND is_active IS DISTINCT FROM (NEW.commercial_status = 'active');
    RETURN NULL;
END
$sync$;

CREATE OR REPLACE TRIGGER trg_sync_product_from_status
AFTER INSERT OR UPDATE OF commercial_status ON products.product_status
FOR EACH ROW
WHEN (pg_trigger_depth() < 1)
EXECUTE FUNCTION products.sync_product_from_status();

-- svc_products writes the catalog but never deletes from it, and never
-- touches image_path (web-managed). Each is a row of contracts/db/grants.csv.
GRANT SELECT, INSERT ON public.product TO svc_products;
GRANT UPDATE (name, category_id, list_price, is_active) ON public.product TO svc_products;

GRANT USAGE ON SCHEMA products_events TO svc_products, definer_products;
GRANT INSERT ON products_events.outbox TO definer_products;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON products_events.outbox TO svc_products;

GRANT USAGE ON SCHEMA api_products
    TO svc_products, svc_sales, svc_recommendations, svc_engagement;
GRANT SELECT ON api_products.product_ref_v1
    TO svc_products, svc_sales, svc_recommendations, svc_engagement;

-- The commercial details. A status is set, never removed; an attribute set is
-- replaced by insert, update of value and delete; a barcode is set or replaced,
-- never cleared; a relation has no column to update, so a replace inserts and
-- deletes. definer_products writes the status from the is_active trigger.
GRANT USAGE ON SCHEMA products TO svc_products, definer_products;
GRANT SELECT, INSERT ON products.product_status TO svc_products, definer_products;
GRANT UPDATE (commercial_status) ON products.product_status TO svc_products, definer_products;
GRANT SELECT, INSERT, DELETE ON products.product_attribute TO svc_products;
GRANT UPDATE (value) ON products.product_attribute TO svc_products;
GRANT SELECT, INSERT ON products.product_barcode TO svc_products;
GRANT UPDATE (barcode) ON products.product_barcode TO svc_products;
GRANT SELECT, INSERT, DELETE ON products.product_relation TO svc_products;

GRANT SELECT ON api_products.product_relation_v1 TO svc_products, svc_recommendations;

-- The bench-only twin serves the details and relations reads too, read-only
-- (ADR-0078): SELECT on the four tables and USAGE on their schema, no write.
GRANT USAGE ON SCHEMA products TO svc_products_bench;
GRANT SELECT ON products.product_status, products.product_attribute,
    products.product_barcode, products.product_relation TO svc_products_bench;
-- END BOUNDARY products

-- BEGIN BOUNDARY customers
-- customers-service (F14-14, F21-14) owns the registration and loyalty-card
-- rule of RN-08: email unique
-- when given, whatever the letter case (ux_customer_email_lower), a known channel
-- (customer_registration_channel_id_fkey), a registration date not in the
-- future (checked by the service) and no deletion. The table stays in public
-- as legacy, and the web only reads it. The boundary publishes no stream, so it
-- has no outbox and no capture trigger.

-- One customer per mailbox, whatever the letter case (#594), as app_user
-- (#288). A plain UNIQUE on email let Ana@ and ana@ register as two customers.
-- The service lower-cases the address it writes; this index is the half a
-- direct INSERT cannot bypass. The DROP is a no-op on a clean build and
-- retires the old constraint on a database loaded before #594.
ALTER TABLE public.customer DROP CONSTRAINT IF EXISTS customer_email_key;
CREATE UNIQUE INDEX IF NOT EXISTS ux_customer_email_lower
    ON public.customer (lower(email));

CREATE SCHEMA IF NOT EXISTS api_customers;
REVOKE ALL ON SCHEMA api_customers FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS customers;
REVOKE ALL ON SCHEMA customers FROM PUBLIC;

-- Loyalty identifiers are durable facts: revocation updates a row and no role
-- receives DELETE. The partial index is the database half of the one-active-
-- card rule and remains effective for writes that bypass customers-service.
CREATE TABLE IF NOT EXISTS customers.loyalty_card (
    card_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_id         UUID NOT NULL REFERENCES public.customer(customer_id),
    code                VARCHAR(26) NOT NULL UNIQUE,
    status              VARCHAR(7) NOT NULL DEFAULT 'ACTIVE',
    issued_at           TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    issued_by           UUID NOT NULL REFERENCES public.app_user(user_id),
    revoked_at          TIMESTAMPTZ,
    revoked_by          UUID REFERENCES public.app_user(user_id),
    revocation_reason   VARCHAR(16),
    CONSTRAINT loyalty_card_code_check
        CHECK (code ~ '^[0-9A-HJKMNP-TV-Z]{26}$'),
    CONSTRAINT loyalty_card_status_check
        CHECK (status IN ('ACTIVE', 'REVOKED')),
    CONSTRAINT loyalty_card_revocation_reason_check
        CHECK (revocation_reason IS NULL OR revocation_reason IN
               ('LOST', 'STOLEN', 'REPLACED', 'CUSTOMER_REQUEST', 'OTHER')),
    CONSTRAINT loyalty_card_revocation_state_check CHECK (
        (status = 'ACTIVE' AND revoked_at IS NULL AND revoked_by IS NULL
                           AND revocation_reason IS NULL)
        OR
        (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_by IS NOT NULL
                            AND revocation_reason IS NOT NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_loyalty_card_one_active_customer
    ON customers.loyalty_card (customer_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_loyalty_card_customer_issued
    ON customers.loyalty_card (customer_id, issued_at DESC, card_id);

DROP TRIGGER IF EXISTS trg_audit_loyalty_card ON customers.loyalty_card;
CREATE TRIGGER trg_audit_loyalty_card
AFTER INSERT OR UPDATE ON customers.loyalty_card
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('card_id');

-- The published customer reference: identity and registration date, and no
-- personal data. security_invoker = off, so a consumer needs no privilege on
-- public.customer.
CREATE OR REPLACE VIEW api_customers.customer_ref_v1
WITH (security_invoker = off) AS
SELECT customer_id, registered_on
FROM public.customer;
REVOKE ALL ON api_customers.customer_ref_v1 FROM PUBLIC;

-- Consumers can resolve one active code but cannot enumerate card data.
CREATE OR REPLACE FUNCTION api_customers.resolve_loyalty_code_v1(p_code text)
RETURNS TABLE (card_id uuid, customer_id uuid)
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public, pg_temp
AS $$
    SELECT card.card_id, card.customer_id
    FROM customers.loyalty_card AS card
    WHERE p_code ~ '^[0-9A-HJKMNP-TV-Z]{26}$'
      AND card.code = p_code
      AND card.status = 'ACTIVE'
$$;
ALTER FUNCTION api_customers.resolve_loyalty_code_v1(text)
    OWNER TO definer_customers;
REVOKE ALL ON FUNCTION api_customers.resolve_loyalty_code_v1(text) FROM PUBLIC;

-- The only service writer of customer: no DELETE, and registered_on and
-- user_id can be set at registration but never corrected.
GRANT SELECT, INSERT ON public.customer TO svc_customers;
GRANT UPDATE (name, email, phone, registration_channel_id)
    ON public.customer TO svc_customers;

GRANT USAGE ON SCHEMA customers TO svc_customers, definer_customers;
GRANT SELECT, INSERT ON customers.loyalty_card TO svc_customers;
GRANT UPDATE (status, revoked_at, revoked_by, revocation_reason)
    ON customers.loyalty_card TO svc_customers;
GRANT SELECT ON customers.loyalty_card TO definer_customers;

GRANT USAGE ON SCHEMA api_customers
    TO svc_customers, svc_sales, svc_preferences, svc_segments,
       svc_recommendations, svc_engagement, svc_checkins;
GRANT SELECT ON api_customers.customer_ref_v1
    TO svc_customers, svc_sales, svc_preferences, svc_segments,
       svc_recommendations, svc_engagement;
GRANT EXECUTE ON FUNCTION api_customers.resolve_loyalty_code_v1(text)
    TO svc_checkins;
-- END BOUNDARY customers

-- BEGIN BOUNDARY segments
-- segments-service (F14-20, RN-20, RN-21, ADR-0034, ADR-0044). It reads the
-- runs the web makes and never writes a segmentation table (RN-05): its
-- contract view of each customer's current label, its outbox for
-- stream:segments.v1.run_recorded and the read grants of svc_segments.

CREATE SCHEMA IF NOT EXISTS api_segments;
REVOKE ALL ON SCHEMA api_segments FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS segments_events;
REVOKE ALL ON SCHEMA segments_events FROM PUBLIC;

-- Keys only, never state: consumers re-read api_segments.current_label_v1.
CREATE TABLE IF NOT EXISTS segments_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON segments_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- contracts/db/api_segments.current_label.v1.md: one row per customer, from
-- the customer's open history row only, never an earlier row or a default.
-- An open row with a label is assigned; one without is the run's own result
-- of no label (RN-21); no open row at all is reported as such. Definer
-- rights, so a consumer needs no privilege on either table.
CREATE OR REPLACE VIEW api_segments.current_label_v1
WITH (security_invoker = off) AS
SELECT c.customer_id,
       CASE
           WHEN h.history_id IS NULL THEN 'no_open_assignment'
           WHEN h.label_code IS NULL THEN 'unassigned_result'
           ELSE 'assigned'
       END::text AS state,
       h.label_code,
       h.valid_from AS since,
       h.run_id
FROM api_customers.customer_ref_v1 AS c
LEFT JOIN public.customer_segment_history AS h
       ON h.customer_id = c.customer_id AND h.valid_to IS NULL;
REVOKE ALL ON api_segments.current_label_v1 FROM PUBLIC;

-- One event per run the web records, so the service and recommendations move
-- their caches to the new run's keys.
CREATE OR REPLACE FUNCTION segments_events.capture_run_recorded() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
BEGIN
    INSERT INTO segments_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:segments.v1.run_recorded', TG_OP, NEW.run_id::text);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('segments_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION segments_events.capture_run_recorded() OWNER TO definer_segments;
REVOKE EXECUTE ON FUNCTION segments_events.capture_run_recorded() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_run_recorded
AFTER INSERT ON public.segmentation_run
FOR EACH ROW EXECUTE FUNCTION segments_events.capture_run_recorded();

-- svc_segments reads the web's segmentation tables and writes none of them.
-- Each is a row of contracts/db/grants.csv.
GRANT SELECT ON public.segment_label, public.segment, public.segment_rule,
    public.segmentation_run, public.customer_segment_history TO svc_segments;

GRANT USAGE ON SCHEMA segments_events TO svc_segments, definer_segments;
GRANT INSERT ON segments_events.outbox TO definer_segments;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON segments_events.outbox TO svc_segments;

GRANT USAGE ON SCHEMA api_segments TO svc_segments, svc_recommendations;
GRANT SELECT ON api_segments.current_label_v1 TO svc_segments, svc_recommendations;
-- END BOUNDARY segments

-- BEGIN BOUNDARY customer-preferences
-- customer-preferences-service (F14-16, RN-54, ADR-0034, ADR-0044). The two
-- 4NF tables stay in public as legacy; the service is their only service
-- writer, and the web only reads them. A replace is a DELETE and an INSERT
-- under pg_advisory_xact_lock(290001, hashtext(customer_id::text))
-- (contracts/db/advisory-locks.md), so the role gets no UPDATE. The capture
-- trigger fires for every writer, so the web's cascades from a customer,
-- channel or category delete are announced too. The tables carry no audit
-- trigger, and this section adds none.

CREATE SCHEMA IF NOT EXISTS preferences;
REVOKE ALL ON SCHEMA preferences FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS api_preferences;
REVOKE ALL ON SCHEMA api_preferences FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS preferences_events;
REVOKE ALL ON SCHEMA preferences_events FROM PUBLIC;

-- Keys only, never state: consumers re-read api_preferences.interest_category_v1.
CREATE TABLE IF NOT EXISTS preferences_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON preferences_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- F21-17 (#795, ADR-0066, RN-73): the consent log, the restriction log and the
-- preference history. All four tables are append-only: svc_preferences holds
-- SELECT and INSERT and nothing else, and a trigger refuses UPDATE, DELETE and
-- TRUNCATE for every role, the owner included (refuse_change, below). A
-- customer is never deleted (RN-08), so the foreign keys to it are RESTRICT.

-- One row per consent decision. The current state of a purpose is its row with
-- the largest decision_id. recorded_by is null exactly for the backfill that
-- gave customers registered before consent existed their programme default.
CREATE TABLE IF NOT EXISTS preferences.consent_decision (
    decision_id BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id UUID NOT NULL,
    purpose VARCHAR(20) NOT NULL,
    decision VARCHAR(20) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    recorded_by UUID,
    source VARCHAR(20) NOT NULL,
    basis VARCHAR(20) NOT NULL,
    CONSTRAINT consent_decision_pkey PRIMARY KEY (decision_id),
    CONSTRAINT consent_decision_customer_id_fkey FOREIGN KEY (customer_id)
        REFERENCES public.customer(customer_id) ON DELETE RESTRICT,
    CONSTRAINT consent_decision_recorded_by_fkey FOREIGN KEY (recorded_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT,
    CONSTRAINT consent_decision_purpose_check
        CHECK (purpose IN ('PERSONALIZATION', 'MARKETING_CONTACT', 'PROFILING')),
    CONSTRAINT consent_decision_decision_check
        CHECK (decision IN ('GRANTED', 'WITHDRAWN')),
    CONSTRAINT consent_decision_source_check
        CHECK (source IN ('STAFF_ON_BEHALF', 'CUSTOMER_SELF', 'MIGRATION')),
    CONSTRAINT consent_decision_basis_check
        CHECK (basis IN ('EXPLICIT', 'PROGRAMME_DEFAULT')),
    CONSTRAINT consent_decision_migration_check
        CHECK ((source = 'MIGRATION') = (recorded_by IS NULL)
           AND (source = 'MIGRATION') = (basis = 'PROGRAMME_DEFAULT'))
);
CREATE INDEX IF NOT EXISTS consent_decision_customer_purpose
    ON preferences.consent_decision (customer_id, purpose, decision_id DESC);

-- One row per restriction decision. A restriction names a channel or an
-- interest category: two nullable references, exactly one set, each with its
-- own foreign key; target_id is the one that is set.
CREATE TABLE IF NOT EXISTS preferences.restriction_decision (
    decision_id BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id UUID NOT NULL,
    kind VARCHAR(20) NOT NULL,
    channel_id SMALLINT,
    category_id SMALLINT,
    target_id SMALLINT GENERATED ALWAYS AS (COALESCE(channel_id, category_id)) STORED,
    decision VARCHAR(20) NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    recorded_by UUID NOT NULL,
    source VARCHAR(20) NOT NULL,
    CONSTRAINT restriction_decision_pkey PRIMARY KEY (decision_id),
    CONSTRAINT restriction_decision_customer_id_fkey FOREIGN KEY (customer_id)
        REFERENCES public.customer(customer_id) ON DELETE RESTRICT,
    CONSTRAINT restriction_decision_channel_id_fkey FOREIGN KEY (channel_id)
        REFERENCES public.channel(channel_id) ON DELETE RESTRICT,
    CONSTRAINT restriction_decision_category_id_fkey FOREIGN KEY (category_id)
        REFERENCES public.category(category_id) ON DELETE RESTRICT,
    CONSTRAINT restriction_decision_recorded_by_fkey FOREIGN KEY (recorded_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT,
    CONSTRAINT restriction_decision_kind_check
        CHECK (kind IN ('CHANNEL', 'INTEREST_CATEGORY')),
    CONSTRAINT restriction_decision_target_check
        CHECK ((kind = 'CHANNEL' AND channel_id IS NOT NULL AND category_id IS NULL)
            OR (kind = 'INTEREST_CATEGORY' AND category_id IS NOT NULL AND channel_id IS NULL)),
    CONSTRAINT restriction_decision_decision_check
        CHECK (decision IN ('IMPOSED', 'LIFTED')),
    CONSTRAINT restriction_decision_source_check
        CHECK (source IN ('STAFF_ON_BEHALF', 'CUSTOMER_SELF'))
);
CREATE INDEX IF NOT EXISTS restriction_decision_customer_target
    ON preferences.restriction_decision (customer_id, kind, target_id, decision_id DESC);
-- The RESTRICT checks when the web deletes a channel or categories-service
-- deletes a category scan the child by these keys.
CREATE INDEX IF NOT EXISTS restriction_decision_channel
    ON preferences.restriction_decision (channel_id) WHERE channel_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS restriction_decision_category
    ON preferences.restriction_decision (category_id) WHERE category_id IS NOT NULL;

-- One row per replace of a declared set that changed it. The ids the set held
-- before and holds after are in preference_change_member, one row per id.
CREATE TABLE IF NOT EXISTS preferences.preference_change (
    change_id BIGINT GENERATED ALWAYS AS IDENTITY,
    customer_id UUID NOT NULL,
    preference_set VARCHAR(20) NOT NULL,
    changed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    changed_by UUID NOT NULL,
    CONSTRAINT preference_change_pkey PRIMARY KEY (change_id),
    CONSTRAINT preference_change_customer_id_fkey FOREIGN KEY (customer_id)
        REFERENCES public.customer(customer_id) ON DELETE RESTRICT,
    CONSTRAINT preference_change_changed_by_fkey FOREIGN KEY (changed_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT,
    CONSTRAINT preference_change_set_check
        CHECK (preference_set IN ('PREFERRED_CHANNELS', 'INTEREST_CATEGORIES'))
);
CREATE INDEX IF NOT EXISTS preference_change_customer
    ON preferences.preference_change (customer_id, change_id DESC);

-- member_id is a channel id or a category id by the change's preference_set. It
-- has no foreign key on purpose: it records what the set held at a past
-- moment, and must stay readable after a channel or category is retired.
CREATE TABLE IF NOT EXISTS preferences.preference_change_member (
    change_id BIGINT NOT NULL,
    side VARCHAR(6) NOT NULL,
    member_id SMALLINT NOT NULL,
    CONSTRAINT preference_change_member_pkey PRIMARY KEY (change_id, side, member_id),
    CONSTRAINT preference_change_member_change_id_fkey FOREIGN KEY (change_id)
        REFERENCES preferences.preference_change(change_id) ON DELETE RESTRICT,
    CONSTRAINT preference_change_member_side_check CHECK (side IN ('BEFORE', 'AFTER'))
);

-- The second layer after the privileges, as for audit_events.domain_event:
-- even the owner's UPDATE, DELETE or TRUNCATE is refused unless an operator's
-- archiving script drops these triggers first.
CREATE OR REPLACE FUNCTION preferences.refuse_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $refuse$
BEGIN
    RAISE EXCEPTION '%.% is append-only (% refused)', TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'check_violation', CONSTRAINT = TG_TABLE_NAME || '_append_only';
END
$refuse$;
CREATE OR REPLACE TRIGGER consent_decision_append_only
BEFORE UPDATE OR DELETE ON preferences.consent_decision
FOR EACH ROW EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER consent_decision_no_truncate
BEFORE TRUNCATE ON preferences.consent_decision
FOR EACH STATEMENT EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER restriction_decision_append_only
BEFORE UPDATE OR DELETE ON preferences.restriction_decision
FOR EACH ROW EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER restriction_decision_no_truncate
BEFORE TRUNCATE ON preferences.restriction_decision
FOR EACH STATEMENT EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER preference_change_append_only
BEFORE UPDATE OR DELETE ON preferences.preference_change
FOR EACH ROW EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER preference_change_no_truncate
BEFORE TRUNCATE ON preferences.preference_change
FOR EACH STATEMENT EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER preference_change_member_append_only
BEFORE UPDATE OR DELETE ON preferences.preference_change_member
FOR EACH ROW EXECUTE FUNCTION preferences.refuse_change();
CREATE OR REPLACE TRIGGER preference_change_member_no_truncate
BEFORE TRUNCATE ON preferences.preference_change_member
FOR EACH STATEMENT EXECUTE FUNCTION preferences.refuse_change();

-- ADR-0066 section 1, the default for customers registered before consent
-- existed: PROFILING and PERSONALIZATION granted, MARKETING_CONTACT withdrawn,
-- each as a MIGRATION row with no actor. It runs only against an empty log, so
-- it gives the customers that exist when the log is created their default and
-- never a customer registered afterwards, who has none until a decision is
-- recorded. On an empty database there is no customer yet and it appends
-- nothing; 02_seed_30_per_table.sql appends the seeded customers' defaults.
INSERT INTO preferences.consent_decision (customer_id, purpose, decision, recorded_by, source, basis)
SELECT c.customer_id, p.purpose, p.decision, NULL, 'MIGRATION', 'PROGRAMME_DEFAULT'
FROM public.customer AS c
CROSS JOIN (VALUES ('PERSONALIZATION', 'GRANTED'),
                   ('MARKETING_CONTACT', 'WITHDRAWN'),
                   ('PROFILING', 'GRANTED')) AS p(purpose, decision)
WHERE NOT EXISTS (SELECT FROM preferences.consent_decision)
ORDER BY c.customer_id, p.purpose;

-- F21-18 (#796, ADR-0066, RN-34, RN-35): the interests inferred from a
-- customer's net purchases, kept apart from the declared ones in
-- public.customer_interest_category, which inference never writes. One row per
-- customer and category; a run replaces a customer's whole set (DELETE and
-- INSERT under the customer's decision lock), so the role gets no UPDATE.
-- Every row says where it came from (source), over how many days (window_days)
-- and how strongly (strength, the percentage of the customer's net purchases
-- in the window that include a product of the category, rounded half up).
-- The rows are derived and can be recomputed, so a category's deletion removes
-- them; a customer is never deleted (RN-08), so that key is RESTRICT. When
-- PROFILING is withdrawn the service deletes the customer's rows in the same
-- transaction as the decision (ADR-0066 section 3).
CREATE TABLE IF NOT EXISTS preferences.inferred_interest (
    customer_id UUID NOT NULL,
    category_id SMALLINT NOT NULL,
    source VARCHAR(20) NOT NULL,
    window_days SMALLINT NOT NULL,
    strength SMALLINT NOT NULL,
    inferred_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $inferred$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_pkey'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_pkey PRIMARY KEY (customer_id, category_id);
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_customer_id_fkey'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_customer_id_fkey FOREIGN KEY (customer_id)
                REFERENCES public.customer(customer_id) ON DELETE RESTRICT;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_category_id_fkey'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_category_id_fkey FOREIGN KEY (category_id)
                REFERENCES public.category(category_id) ON DELETE CASCADE;
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_source_check'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_source_check CHECK (source IN ('NET_PURCHASES'));
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_window_days_check'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_window_days_check
                CHECK (window_days BETWEEN 1 AND 3650);
    END IF;
    IF NOT EXISTS (SELECT FROM pg_constraint WHERE conname = 'inferred_interest_strength_check'
                   AND conrelid = 'preferences.inferred_interest'::regclass) THEN
        ALTER TABLE preferences.inferred_interest
            ADD CONSTRAINT inferred_interest_strength_check CHECK (strength BETWEEN 1 AND 100);
    END IF;
END
$inferred$;
CREATE INDEX IF NOT EXISTS inferred_interest_category
    ON preferences.inferred_interest (category_id);

-- contracts/db/api_preferences.interest_category.v1.md. Definer rights
-- (security_invoker = off): recommendations reads these two columns and needs
-- no privilege on the base table.
CREATE OR REPLACE VIEW api_preferences.interest_category_v1
WITH (security_invoker = off) AS
SELECT customer_id, category_id
FROM public.customer_interest_category;
REVOKE ALL ON api_preferences.interest_category_v1 FROM PUBLIC;

-- contracts/db/api_preferences.consent_state.v1.md: the latest decision of each
-- purpose, by decision_id. No actor and no source: reading a customer's state
-- is not reading the log.
CREATE OR REPLACE VIEW api_preferences.consent_state_v1
WITH (security_invoker = off) AS
SELECT DISTINCT ON (d.customer_id, d.purpose)
       d.customer_id, d.purpose, d.decision, d.decision_id, d.recorded_at, d.basis
FROM preferences.consent_decision AS d
ORDER BY d.customer_id, d.purpose, d.decision_id DESC;
REVOKE ALL ON api_preferences.consent_state_v1 FROM PUBLIC;

-- contracts/db/api_preferences.restriction_v1.md: a row while the latest
-- decision for the target is IMPOSED.
CREATE OR REPLACE VIEW api_preferences.restriction_v1
WITH (security_invoker = off) AS
SELECT latest.customer_id, latest.kind, latest.target_id, latest.decision_id,
       latest.recorded_at AS imposed_at
FROM (
    SELECT DISTINCT ON (d.customer_id, d.kind, d.target_id)
           d.customer_id, d.kind, d.target_id, d.decision, d.decision_id, d.recorded_at
    FROM preferences.restriction_decision AS d
    ORDER BY d.customer_id, d.kind, d.target_id, d.decision_id DESC
) AS latest
WHERE latest.decision = 'IMPOSED';
REVOKE ALL ON api_preferences.restriction_v1 FROM PUBLIC;

-- The web's batch segmentation cannot read api_preferences (ADR-0046) and
-- consumes no service, so it reads this read-only view with the same
-- definition: the database route of ADR-0045 (ADR-0066 section 2). SELECT
-- only; the default privileges of public must not make it writable.
CREATE OR REPLACE VIEW public.consent_state
WITH (security_invoker = off) AS
SELECT customer_id, purpose, decision, decision_id, recorded_at, basis
FROM api_preferences.consent_state_v1;
REVOKE ALL ON public.consent_state FROM PUBLIC, retail_app;
GRANT SELECT ON public.consent_state TO retail_app;

-- Captures every writer of either set, the web included, in the writer's
-- transaction. entity_id is the customer whose profile changed (ADR-0044).
CREATE OR REPLACE FUNCTION preferences_events.capture_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        entity := OLD.customer_id::text;
    ELSE
        entity := NEW.customer_id::text;
    END IF;
    INSERT INTO preferences_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:preferences.v1.changed', TG_OP, entity);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('preferences_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION preferences_events.capture_changed() OWNER TO definer_preferences;
REVOKE EXECUTE ON FUNCTION preferences_events.capture_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_preferred_channel_changed
AFTER INSERT OR DELETE ON public.customer_preferred_channel
FOR EACH ROW EXECUTE FUNCTION preferences_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_interest_category_changed
AFTER INSERT OR DELETE ON public.customer_interest_category
FOR EACH ROW EXECUTE FUNCTION preferences_events.capture_changed();

-- The consent stream (contracts/streams/preferences.consent_changed.v1.md):
-- one key-only event per row appended to either log, in the transaction that
-- appends it, so a rollback announces nothing and the views already show the
-- change when the event is visible.
CREATE OR REPLACE FUNCTION preferences_events.capture_consent_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
BEGIN
    INSERT INTO preferences_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:preferences.v1.consent_changed', TG_OP, NEW.customer_id::text);
    PERFORM pg_notify('preferences_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION preferences_events.capture_consent_changed() OWNER TO definer_preferences;
REVOKE EXECUTE ON FUNCTION preferences_events.capture_consent_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_consent_decision_changed
AFTER INSERT ON preferences.consent_decision
FOR EACH ROW EXECUTE FUNCTION preferences_events.capture_consent_changed();
CREATE OR REPLACE TRIGGER trg_capture_restriction_decision_changed
AFTER INSERT ON preferences.restriction_decision
FOR EACH ROW EXECUTE FUNCTION preferences_events.capture_consent_changed();

-- Whole-set replace: SELECT, INSERT and DELETE, never UPDATE. The customer is
-- checked through api_customers.customer_ref_v1 (granted in the customers
-- section), and channels and categories by foreign key, which needs no
-- privilege on the parent (E-11).
GRANT SELECT, INSERT, DELETE ON public.customer_preferred_channel TO svc_preferences;
GRANT SELECT, INSERT, DELETE ON public.customer_interest_category TO svc_preferences;

GRANT USAGE ON SCHEMA preferences_events TO svc_preferences, definer_preferences;
GRANT INSERT ON preferences_events.outbox TO definer_preferences;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON preferences_events.outbox TO svc_preferences;

GRANT USAGE ON SCHEMA preferences TO svc_preferences;
GRANT SELECT, INSERT ON preferences.consent_decision, preferences.restriction_decision,
    preferences.preference_change, preferences.preference_change_member TO svc_preferences;
-- F21-18: inference replaces a customer's inferred set whole, so SELECT, INSERT
-- and DELETE, never UPDATE. It reads what a customer bought and the category of
-- each product only through the published views of sales and products.
GRANT SELECT, INSERT, DELETE ON preferences.inferred_interest TO svc_preferences;
GRANT USAGE ON SCHEMA api_sales, api_products TO svc_preferences;
GRANT SELECT ON api_sales.net_purchase_line_v1 TO svc_preferences;
GRANT SELECT ON api_products.product_ref_v1 TO svc_preferences;

GRANT USAGE ON SCHEMA api_preferences TO svc_preferences, svc_recommendations, svc_campaigns;
GRANT SELECT ON api_preferences.interest_category_v1 TO svc_preferences, svc_recommendations;
-- The readers named by contracts/db/api_preferences.consent_state.v1.md and
-- restriction.v1.md whose roles exist; rfm-engine's follows with its role.
GRANT SELECT ON api_preferences.consent_state_v1 TO svc_preferences, svc_recommendations, svc_campaigns;
GRANT SELECT ON api_preferences.restriction_v1 TO svc_preferences, svc_recommendations, svc_campaigns;
-- END BOUNDARY customer-preferences

-- BEGIN BOUNDARY categories
-- categories-service (F14-08) owns the category tree: no cycle (RN-33,
-- trg_category_no_cycle), and no service delete of a category that has
-- products (RN-06) or children (RN-07), both ON DELETE RESTRICT, or that a
-- customer names as an interest (E-13, trg_category_delete_guard below). The
-- table stays in public; the web remains a legacy co-writer (E-09), so the
-- capture trigger below sees its edits and hard deletes too.

CREATE SCHEMA IF NOT EXISTS api_categories;
REVOKE ALL ON SCHEMA api_categories FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS categories_events;
REVOKE ALL ON SCHEMA categories_events FROM PUBLIC;

-- Keys only, never state: consumers re-read the producer's api_ view.
CREATE TABLE IF NOT EXISTS categories_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON categories_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- The published tree: one row per category, with no privilege needed on the
-- base table. security_invoker = off.
CREATE OR REPLACE VIEW api_categories.category_tree_v1
WITH (security_invoker = off) AS
SELECT category_id, parent_category_id, name
FROM public.category;
REVOKE ALL ON api_categories.category_tree_v1 FROM PUBLIC;

-- Captures every writer, the web included, in the writer's transaction.
CREATE OR REPLACE FUNCTION categories_events.capture_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        entity := OLD.category_id::text;
    ELSE
        entity := NEW.category_id::text;
    END IF;
    INSERT INTO categories_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:categories.v1.changed', TG_OP, entity);
    -- An empty wake-up for the owner's relay; only that relay LISTENs.
    PERFORM pg_notify('categories_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION categories_events.capture_changed() OWNER TO definer_categories;
REVOKE EXECUTE ON FUNCTION categories_events.capture_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_changed
AFTER INSERT OR UPDATE OR DELETE ON public.category
FOR EACH ROW EXECUTE FUNCTION categories_events.capture_changed();

-- E-13: customer_interest_category cascades on a category delete, and that
-- table belongs to customer-preferences. A service session (session_user is a
-- svc_* role, which SET ROLE cannot change) may not delete a category a
-- customer names; the web's own cascade is untouched. The function runs as
-- definer_guard, whose only privilege is SELECT on that one table, so no
-- service gains a privilege on it. The refusal is a foreign-key-class error
-- under its own constraint name, which the service maps to 409.
CREATE OR REPLACE FUNCTION fn_category_delete_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $guard$
BEGIN
    IF session_user LIKE 'svc\_%' AND EXISTS (
        SELECT FROM public.customer_interest_category AS i
        WHERE i.category_id = OLD.category_id
    ) THEN
        RAISE EXCEPTION 'category % is named as a customer interest', OLD.category_id
            USING ERRCODE = 'foreign_key_violation',
                  CONSTRAINT = 'category_has_customer_interest';
    END IF;
    RETURN OLD;
END
$guard$;
ALTER FUNCTION fn_category_delete_guard() OWNER TO definer_guard;
REVOKE EXECUTE ON FUNCTION fn_category_delete_guard() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_category_delete_guard
BEFORE DELETE ON public.category
FOR EACH ROW EXECUTE FUNCTION fn_category_delete_guard();

GRANT SELECT ON public.customer_interest_category TO definer_guard;

GRANT SELECT, INSERT, UPDATE, DELETE ON public.category TO svc_categories;

GRANT USAGE ON SCHEMA categories_events TO svc_categories, definer_categories;
GRANT INSERT ON categories_events.outbox TO definer_categories;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON categories_events.outbox TO svc_categories;

GRANT USAGE ON SCHEMA api_categories TO svc_categories, svc_recommendations;
GRANT SELECT ON api_categories.category_tree_v1 TO svc_categories, svc_recommendations;
-- END BOUNDARY categories

-- BEGIN BOUNDARY stock
CREATE SCHEMA IF NOT EXISTS api_stock;
REVOKE ALL ON SCHEMA api_stock FROM PUBLIC;

CREATE SCHEMA IF NOT EXISTS stock_events;
REVOKE ALL ON SCHEMA stock_events FROM PUBLIC;

-- Keys only, never state: consumers re-read api_stock.stock_level_v1.
CREATE TABLE IF NOT EXISTS stock_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON stock_events.outbox (occurred_at) WHERE relayed_at IS NULL;

CREATE OR REPLACE VIEW api_stock.stock_level_v1
WITH (security_invoker = false)
AS
SELECT store_id, product_id, quantity_on_hand, updated_at
FROM public.inventory;

CREATE OR REPLACE FUNCTION api_stock.consume_v1(
    requested_store_id smallint,
    requested_product_id integer,
    requested_quantity integer
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $consume$
DECLARE
    remaining integer;
BEGIN
    IF $3 IS NULL OR $3 <= 0 THEN
        RAISE EXCEPTION 'quantity must be greater than zero'
            USING ERRCODE = '22023';
    END IF;

    UPDATE inventory
       SET quantity_on_hand = quantity_on_hand - $3,
           updated_at = now()
     WHERE store_id = $1
       AND product_id = $2
       AND quantity_on_hand >= $3
     RETURNING quantity_on_hand INTO remaining;

    IF FOUND THEN
        RETURN remaining;
    END IF;

    SELECT quantity_on_hand
      INTO remaining
      FROM inventory
     WHERE store_id = $1 AND product_id = $2
       FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no stock record for store % and product %', $1, $2
            USING ERRCODE = '23514', CONSTRAINT = 'no_stock_record';
    END IF;

    RAISE EXCEPTION 'only % units are available', remaining
        USING ERRCODE = '23514', CONSTRAINT = 'stock_unavailable';
END
$consume$;
ALTER FUNCTION api_stock.consume_v1(smallint, integer, integer) OWNER TO definer_stock;
REVOKE EXECUTE ON FUNCTION api_stock.consume_v1(smallint, integer, integer) FROM PUBLIC;

-- Captures every inventory writer, including the frozen web, in its transaction.
CREATE OR REPLACE FUNCTION stock_events.capture_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        entity := OLD.store_id::text || ':' || OLD.product_id::text;
    ELSE
        entity := NEW.store_id::text || ':' || NEW.product_id::text;
    END IF;
    INSERT INTO stock_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:stock.v1.changed', TG_OP, entity);
    PERFORM pg_notify('stock_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION stock_events.capture_changed() OWNER TO definer_stock;
REVOKE EXECUTE ON FUNCTION stock_events.capture_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_changed
AFTER INSERT OR UPDATE OR DELETE ON public.inventory
FOR EACH ROW EXECUTE FUNCTION stock_events.capture_changed();

GRANT SELECT ON public.inventory TO svc_stock, definer_stock;
GRANT UPDATE (quantity_on_hand, updated_at) ON public.inventory TO definer_stock;

GRANT USAGE ON SCHEMA stock_events TO svc_stock, definer_stock;
GRANT INSERT ON stock_events.outbox TO definer_stock;
GRANT SELECT, DELETE ON stock_events.outbox TO svc_stock;
GRANT UPDATE (relayed_at) ON stock_events.outbox TO svc_stock;

GRANT USAGE ON SCHEMA api_stock TO svc_stock, svc_recommendations, svc_sales;
GRANT SELECT ON api_stock.stock_level_v1 TO svc_stock, svc_recommendations;
GRANT EXECUTE ON FUNCTION api_stock.consume_v1(smallint, integer, integer) TO svc_sales;
-- ---------- stock movements (F21-06, ADR-0061, ADR-0046) ----------
-- A recorded movement is the only way beyond a sale or its reversal to change
-- on-hand, so the movement and its inventory change commit together or not at
-- all. Tables live in the stock service schema, never in public.
CREATE SCHEMA IF NOT EXISTS stock;
REVOKE ALL ON SCHEMA stock FROM PUBLIC;

-- One row per accepted request, kept so a retry is recognised. The key is a
-- UUID the caller makes once per movement; request_sha256 is the hash of the
-- caller's sub and the movement's fields in canonical form, so the same key
-- with other fields is refused. Only accepted requests are written: a refused
-- one rolls this row back with everything else (ADR-0061).
CREATE TABLE IF NOT EXISTS stock.movement_request (
    idempotency_key uuid PRIMARY KEY,
    request_sha256  bytea NOT NULL
        CONSTRAINT movement_request_sha256_check
        CHECK (octet_length(request_sha256) = 32),
    recorded_by     uuid NOT NULL
        REFERENCES public.app_user (user_id) ON DELETE RESTRICT,
    accepted_at     timestamptz NOT NULL DEFAULT now()
);

-- Append-only: no service role holds UPDATE or DELETE. A mistake is corrected
-- by a later movement. A transfer is two rows sharing transfer_id and one
-- request. before and after are facts of the moment, not functions of current
-- inventory, so they are stored. reversal_id names a sale reversal without a
-- foreign key: reversals are recorded by sales, in its own boundary.
CREATE TABLE IF NOT EXISTS stock.stock_movement (
    movement_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key  uuid
        REFERENCES stock.movement_request (idempotency_key) ON DELETE RESTRICT,
    movement_type    text NOT NULL
        CONSTRAINT stock_movement_type_check
        CHECK (movement_type IN ('RECEIPT', 'TRANSFER', 'COUNT', 'ADJUSTMENT', 'RESTOCK')),
    store_id         smallint NOT NULL
        REFERENCES public.store (store_id) ON DELETE RESTRICT,
    product_id       integer NOT NULL
        REFERENCES public.product (product_id) ON DELETE RESTRICT,
    quantity_delta   integer NOT NULL,
    quantity_before  integer NOT NULL
        CONSTRAINT stock_movement_before_check CHECK (quantity_before >= 0),
    quantity_after   integer NOT NULL
        CONSTRAINT stock_movement_after_check CHECK (quantity_after >= 0),
    transfer_id      uuid,
    reference        text,
    reason           text
        CONSTRAINT stock_movement_reason_check
        CHECK (reason IS NULL OR reason IN ('DAMAGE', 'LOSS', 'EXPIRY', 'CORRECTION')),
    counted_by       text,
    note             text,
    reversal_id      bigint,
    recorded_by      uuid
        REFERENCES public.app_user (user_id) ON DELETE RESTRICT,
    recorded_at      timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT stock_movement_balance_check
        CHECK (quantity_after::bigint = quantity_before::bigint + quantity_delta::bigint),
    -- What each type must state, and what it must not carry.
    CONSTRAINT stock_movement_shape_check CHECK (
        CASE movement_type
            WHEN 'RECEIPT' THEN quantity_delta > 0 AND btrim(coalesce(reference, '')) <> ''
                AND transfer_id IS NULL AND reason IS NULL AND counted_by IS NULL
                AND reversal_id IS NULL AND recorded_by IS NOT NULL
            WHEN 'TRANSFER' THEN quantity_delta <> 0 AND transfer_id IS NOT NULL
                AND reference IS NULL AND reason IS NULL AND counted_by IS NULL
                AND reversal_id IS NULL AND recorded_by IS NOT NULL
            WHEN 'COUNT' THEN btrim(coalesce(counted_by, '')) <> ''
                AND transfer_id IS NULL AND reference IS NULL AND reason IS NULL
                AND reversal_id IS NULL AND recorded_by IS NOT NULL
            WHEN 'ADJUSTMENT' THEN quantity_delta <> 0 AND reason IS NOT NULL
                AND transfer_id IS NULL AND reference IS NULL AND counted_by IS NULL
                AND reversal_id IS NULL AND recorded_by IS NOT NULL
            WHEN 'RESTOCK' THEN quantity_delta > 0 AND reversal_id IS NOT NULL
                AND transfer_id IS NULL AND reference IS NULL AND reason IS NULL
                AND counted_by IS NULL AND recorded_by IS NULL
        END
    ),
    -- Every row but a restock came from an accepted request.
    CONSTRAINT stock_movement_request_check
        CHECK ((movement_type = 'RESTOCK') = (idempotency_key IS NULL))
);

-- A transfer holds one row per store, and a reversal restocks a pair once
-- (api_stock.restock_v1 names the second index in its error contract).
CREATE UNIQUE INDEX IF NOT EXISTS stock_movement_transfer_store
    ON stock.stock_movement (transfer_id, store_id) WHERE transfer_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS restock_already_recorded
    ON stock.stock_movement (reversal_id, store_id, product_id) WHERE movement_type = 'RESTOCK';
CREATE INDEX IF NOT EXISTS stock_movement_recorded
    ON stock.stock_movement (recorded_at DESC, movement_id DESC);
CREATE INDEX IF NOT EXISTS stock_movement_pair_recorded
    ON stock.stock_movement (store_id, product_id, recorded_at DESC);
CREATE INDEX IF NOT EXISTS stock_movement_request
    ON stock.stock_movement (idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Changes one inventory row by delta and says what it held before and after.
-- Subtracting is a conditional UPDATE, as consume_v1 does, so two movements on
-- one row add up and nothing goes below zero. Adding is one upsert, so the
-- record is created when absent and a concurrent add cannot be lost. Not
-- SECURITY DEFINER itself: it runs only inside record_movement, as its owner.
CREATE OR REPLACE FUNCTION stock.apply_delta(
    p_store_id smallint,
    p_product_id integer,
    p_delta integer,
    OUT quantity_before integer,
    OUT quantity_after integer
) RETURNS record
LANGUAGE plpgsql
SET search_path = public, pg_temp
AS $apply$
BEGIN
    IF p_delta > 0 THEN
        INSERT INTO inventory AS i (store_id, product_id, quantity_on_hand)
        VALUES (p_store_id, p_product_id, p_delta)
        ON CONFLICT (store_id, product_id) DO UPDATE
           SET quantity_on_hand = i.quantity_on_hand + EXCLUDED.quantity_on_hand,
               updated_at = now()
        RETURNING i.quantity_on_hand INTO quantity_after;
        quantity_before := quantity_after - p_delta;
        RETURN;
    END IF;

    UPDATE inventory AS i
       SET quantity_on_hand = i.quantity_on_hand + p_delta,
           updated_at = now()
     WHERE i.store_id = p_store_id
       AND i.product_id = p_product_id
       AND i.quantity_on_hand >= -p_delta
     RETURNING i.quantity_on_hand INTO quantity_after;
    IF FOUND THEN
        quantity_before := quantity_after - p_delta;
        RETURN;
    END IF;

    SELECT i.quantity_on_hand INTO quantity_before
      FROM inventory AS i
     WHERE i.store_id = p_store_id AND i.product_id = p_product_id
       FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'no stock record for store % and product %', p_store_id, p_product_id
            USING ERRCODE = '23514', CONSTRAINT = 'no_stock_record';
    END IF;
    RAISE EXCEPTION 'only % units are available', quantity_before
        USING ERRCODE = '23514', CONSTRAINT = 'stock_unavailable';
END
$apply$;
ALTER FUNCTION stock.apply_delta(smallint, integer, integer) OWNER TO definer_stock;
REVOKE EXECUTE ON FUNCTION stock.apply_delta(smallint, integer, integer) FROM PUBLIC;

-- Applies one request: the movement row(s) and the inventory change in the
-- caller's one transaction. Returns the movement ids, a transfer's origin
-- first, and whether the key had already been accepted. A replay with the same
-- canonical request changes nothing; the same key with another one is refused
-- (23505, idempotency_key_reused). The caller validates what each type must
-- state; the table's CHECKs are the second line. A transfer touches its two
-- rows in ascending store order, so two opposite transfers cannot deadlock.
CREATE OR REPLACE FUNCTION stock.record_movement(
    p_idempotency_key uuid,
    p_request text,
    p_recorded_by uuid,
    p_movement_type text,
    p_store_id smallint,
    p_destination_store_id smallint,
    p_product_id integer,
    p_quantity integer,
    p_reference text,
    p_reason text,
    p_counted_by text,
    p_note text,
    OUT movement_ids bigint[],
    OUT replayed boolean
) RETURNS record
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $record$
DECLARE
    hash bytea := sha256(convert_to(p_request, 'UTF8'));
    stored bytea;
    before_quantity integer;
    applied record;
    destination record;
    origin record;
    transfer uuid;
    id bigint;
BEGIN
    IF p_idempotency_key IS NULL OR p_recorded_by IS NULL OR p_request IS NULL
       OR p_store_id IS NULL OR p_product_id IS NULL OR p_quantity IS NULL THEN
        RAISE EXCEPTION 'a movement needs its key, author, store, product and quantity'
            USING ERRCODE = '22023';
    END IF;

    INSERT INTO stock.movement_request (idempotency_key, request_sha256, recorded_by)
    VALUES (p_idempotency_key, hash, p_recorded_by)
    ON CONFLICT (idempotency_key) DO NOTHING;
    IF NOT FOUND THEN
        SELECT r.request_sha256 INTO stored
          FROM stock.movement_request AS r
         WHERE r.idempotency_key = p_idempotency_key;
        IF stored IS DISTINCT FROM hash THEN
            RAISE EXCEPTION 'the idempotency key was used for a different movement'
                USING ERRCODE = '23505', CONSTRAINT = 'idempotency_key_reused';
        END IF;
        SELECT array_agg(m.movement_id ORDER BY m.movement_id) INTO movement_ids
          FROM stock.stock_movement AS m
         WHERE m.idempotency_key = p_idempotency_key;
        replayed := true;
        RETURN;
    END IF;

    movement_ids := ARRAY[]::bigint[];
    replayed := false;

    IF p_movement_type = 'RECEIPT' THEN
        IF p_quantity <= 0 THEN
            RAISE EXCEPTION 'quantity must be greater than zero' USING ERRCODE = '22023';
        END IF;
        applied := stock.apply_delta(p_store_id, p_product_id, p_quantity);
        INSERT INTO stock.stock_movement (idempotency_key, movement_type, store_id, product_id,
               quantity_delta, quantity_before, quantity_after, reference, note, recorded_by)
        VALUES (p_idempotency_key, 'RECEIPT', p_store_id, p_product_id, p_quantity,
                applied.quantity_before, applied.quantity_after, p_reference, p_note,
                p_recorded_by)
        RETURNING movement_id INTO id;
        movement_ids := movement_ids || id;

    ELSIF p_movement_type = 'ADJUSTMENT' THEN
        IF p_quantity = 0 THEN
            RAISE EXCEPTION 'an adjustment changes the quantity' USING ERRCODE = '22023';
        END IF;
        applied := stock.apply_delta(p_store_id, p_product_id, p_quantity);
        INSERT INTO stock.stock_movement (idempotency_key, movement_type, store_id, product_id,
               quantity_delta, quantity_before, quantity_after, reason, note, recorded_by)
        VALUES (p_idempotency_key, 'ADJUSTMENT', p_store_id, p_product_id, p_quantity,
                applied.quantity_before, applied.quantity_after, p_reason, p_note,
                p_recorded_by)
        RETURNING movement_id INTO id;
        movement_ids := movement_ids || id;

    ELSIF p_movement_type = 'COUNT' THEN
        IF p_quantity < 0 THEN
            RAISE EXCEPTION 'a count is zero or more' USING ERRCODE = '22023';
        END IF;
        INSERT INTO inventory (store_id, product_id, quantity_on_hand)
        VALUES (p_store_id, p_product_id, p_quantity)
        ON CONFLICT (store_id, product_id) DO NOTHING;
        IF FOUND THEN
            before_quantity := 0;
        ELSE
            SELECT i.quantity_on_hand INTO before_quantity
              FROM inventory AS i
             WHERE i.store_id = p_store_id AND i.product_id = p_product_id
               FOR UPDATE;
            UPDATE inventory AS i
               SET quantity_on_hand = p_quantity, updated_at = now()
             WHERE i.store_id = p_store_id AND i.product_id = p_product_id;
        END IF;
        INSERT INTO stock.stock_movement (idempotency_key, movement_type, store_id, product_id,
               quantity_delta, quantity_before, quantity_after, counted_by, note, recorded_by)
        VALUES (p_idempotency_key, 'COUNT', p_store_id, p_product_id,
                p_quantity - before_quantity, before_quantity, p_quantity, p_counted_by, p_note,
                p_recorded_by)
        RETURNING movement_id INTO id;
        movement_ids := movement_ids || id;

    ELSIF p_movement_type = 'TRANSFER' THEN
        IF p_destination_store_id IS NULL OR p_quantity <= 0 THEN
            RAISE EXCEPTION 'a transfer needs a destination and a positive quantity'
                USING ERRCODE = '22023';
        END IF;
        IF p_destination_store_id = p_store_id THEN
            RAISE EXCEPTION 'a transfer needs two different stores' USING ERRCODE = '22023';
        END IF;
        transfer := gen_random_uuid();
        IF p_store_id < p_destination_store_id THEN
            origin := stock.apply_delta(p_store_id, p_product_id, -p_quantity);
            destination := stock.apply_delta(p_destination_store_id, p_product_id, p_quantity);
        ELSE
            destination := stock.apply_delta(p_destination_store_id, p_product_id, p_quantity);
            origin := stock.apply_delta(p_store_id, p_product_id, -p_quantity);
        END IF;
        INSERT INTO stock.stock_movement (idempotency_key, movement_type, store_id, product_id,
               quantity_delta, quantity_before, quantity_after, transfer_id, note, recorded_by)
        VALUES (p_idempotency_key, 'TRANSFER', p_store_id, p_product_id, -p_quantity,
                origin.quantity_before, origin.quantity_after, transfer, p_note, p_recorded_by)
        RETURNING movement_id INTO id;
        movement_ids := movement_ids || id;
        INSERT INTO stock.stock_movement (idempotency_key, movement_type, store_id, product_id,
               quantity_delta, quantity_before, quantity_after, transfer_id, note, recorded_by)
        VALUES (p_idempotency_key, 'TRANSFER', p_destination_store_id, p_product_id, p_quantity,
                destination.quantity_before, destination.quantity_after, transfer, p_note,
                p_recorded_by)
        RETURNING movement_id INTO id;
        movement_ids := movement_ids || id;

    ELSE
        RAISE EXCEPTION 'unknown movement type %', p_movement_type USING ERRCODE = '22023';
    END IF;
END
$record$;
ALTER FUNCTION stock.record_movement(uuid, text, uuid, text, smallint, smallint, integer, integer,
    text, text, text, text) OWNER TO definer_stock;
REVOKE EXECUTE ON FUNCTION stock.record_movement(uuid, text, uuid, text, smallint, smallint,
    integer, integer, text, text, text, text) FROM PUBLIC;

-- contracts/db/api_stock.restock.v1.md. Sales calls it once per resalable
-- reversed line, inside the reversal's transaction (ADR-0061, ADR-0062, E-05).
-- It only adds, and only to an existing row: a missing pair is no_stock_record,
-- never a new record. The RESTOCK row and the quantity change are one
-- statement block, so a second restock of the same (reversal, store, product)
-- fails on restock_already_recorded and takes its UPDATE with it.
CREATE OR REPLACE FUNCTION api_stock.restock_v1(
    requested_store_id smallint,
    requested_product_id integer,
    requested_quantity integer,
    requested_reversal_id bigint
) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $restock$
DECLARE
    restored integer;
BEGIN
    IF $3 IS NULL OR $3 <= 0 OR $4 IS NULL THEN
        RAISE EXCEPTION 'quantity must be greater than zero and the reversal named'
            USING ERRCODE = '22023';
    END IF;

    UPDATE inventory
       SET quantity_on_hand = quantity_on_hand + $3,
           updated_at = now()
     WHERE store_id = $1
       AND product_id = $2
     RETURNING quantity_on_hand INTO restored;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'no stock record for store % and product %', $1, $2
            USING ERRCODE = '23514', CONSTRAINT = 'no_stock_record';
    END IF;

    INSERT INTO stock.stock_movement (movement_type, store_id, product_id, quantity_delta,
           quantity_before, quantity_after, reversal_id)
    VALUES ('RESTOCK', $1, $2, $3, restored - $3, restored, $4);

    RETURN restored;
END
$restock$;
ALTER FUNCTION api_stock.restock_v1(smallint, integer, integer, bigint) OWNER TO definer_stock;
REVOKE EXECUTE ON FUNCTION api_stock.restock_v1(smallint, integer, integer, bigint) FROM PUBLIC;

-- A movement is stock-service's own: only svc_stock reads the history, and
-- only definer_stock writes it. Each is a row of contracts/db/grants.csv.
GRANT USAGE ON SCHEMA stock TO svc_stock, definer_stock;
GRANT SELECT ON stock.stock_movement TO svc_stock;
GRANT SELECT, INSERT ON stock.stock_movement, stock.movement_request TO definer_stock;
GRANT INSERT ON public.inventory TO definer_stock;
GRANT EXECUTE ON FUNCTION stock.record_movement(uuid, text, uuid, text, smallint, smallint,
    integer, integer, text, text, text, text) TO svc_stock;
GRANT EXECUTE ON FUNCTION stock.apply_delta(smallint, integer, integer) TO definer_stock;
GRANT EXECUTE ON FUNCTION api_stock.restock_v1(smallint, integer, integer, bigint) TO svc_sales;
-- END BOUNDARY stock

-- BEGIN BOUNDARY stores
CREATE SCHEMA IF NOT EXISTS stores;
CREATE SCHEMA IF NOT EXISTS api_stores;
CREATE SCHEMA IF NOT EXISTS stores_events;
REVOKE ALL ON SCHEMA stores, api_stores, stores_events FROM PUBLIC;

CREATE TABLE IF NOT EXISTS stores.store_location (
    store_id SMALLINT NOT NULL,
    latitude NUMERIC NOT NULL,
    longitude NUMERIC NOT NULL,
    geofence_radius_m INTEGER NOT NULL,
    set_by UUID NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every named constraint is added separately so this boundary is safe to
-- re-apply to a database that already contains the table.
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_pkey'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_pkey
        PRIMARY KEY (store_id); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_store_id_fkey'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_store_id_fkey
        FOREIGN KEY (store_id) REFERENCES public.store(store_id) ON DELETE CASCADE; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_latitude_check'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_latitude_check
        CHECK (latitude BETWEEN -90 AND 90); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_longitude_check'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_longitude_check
        CHECK (longitude BETWEEN -180 AND 180); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_geofence_radius_m_check'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_geofence_radius_m_check
        CHECK (geofence_radius_m BETWEEN 25 AND 2000); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'stores.store_location'::regclass
      AND conname = 'store_location_set_by_fkey'
) THEN ALTER TABLE stores.store_location ADD CONSTRAINT store_location_set_by_fkey
        FOREIGN KEY (set_by) REFERENCES public.app_user(user_id) ON DELETE RESTRICT; END IF; END $$;

-- F21-12 (#786, RN-53): the other four facts of a store that only ADMIN sets
-- through stores-service. Each is its own relation keyed by store_id, so a
-- fact that has many values (opening days, enabled channels) never shares a
-- row with another (4NF, docs/data-model.md). A store whose row is absent has
-- never been written: it is OPEN, has no hours, enables no channel and has no
-- discount cap (api_stores.store_directory_v2). public.store.is_active is a
-- different fact, written only by the web.
CREATE TABLE IF NOT EXISTS stores.store_status (
    store_id SMALLINT NOT NULL,
    status TEXT NOT NULL,
    set_by UUID NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT store_status_pkey PRIMARY KEY (store_id),
    CONSTRAINT store_status_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES public.store(store_id) ON DELETE CASCADE,
    CONSTRAINT store_status_status_check
        CHECK (status IN ('OPEN', 'TEMPORARILY_CLOSED', 'CLOSED')),
    CONSTRAINT store_status_set_by_fkey FOREIGN KEY (set_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT
);

-- One row per store whose hours were ever written. time_zone is NULL exactly
-- when the store has no interval (the hours were cleared), which the
-- constraint trigger below holds at commit.
CREATE TABLE IF NOT EXISTS stores.store_hours (
    store_id SMALLINT NOT NULL,
    time_zone TEXT,
    set_by UUID NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT store_hours_pkey PRIMARY KEY (store_id),
    CONSTRAINT store_hours_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES public.store(store_id) ON DELETE CASCADE,
    CONSTRAINT store_hours_time_zone_check
        CHECK (time_zone IS NULL OR char_length(time_zone) BETWEEN 1 AND 64),
    CONSTRAINT store_hours_set_by_fkey FOREIGN KEY (set_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT
);

-- A day with no row is a closed day. No interval crosses midnight.
CREATE TABLE IF NOT EXISTS stores.store_hours_interval (
    store_id SMALLINT NOT NULL,
    day_of_week SMALLINT NOT NULL,
    opens_at TIME NOT NULL,
    closes_at TIME NOT NULL,
    CONSTRAINT store_hours_interval_pkey PRIMARY KEY (store_id, day_of_week),
    CONSTRAINT store_hours_interval_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES stores.store_hours(store_id) ON DELETE CASCADE,
    CONSTRAINT store_hours_interval_day_of_week_check
        CHECK (day_of_week BETWEEN 1 AND 7),
    CONSTRAINT store_hours_interval_minutes_check
        CHECK (extract(second FROM opens_at) = 0 AND extract(second FROM closes_at) = 0),
    CONSTRAINT store_hours_interval_order_check CHECK (closes_at > opens_at)
);

-- One row per store whose channel set was ever written, so an emptied set
-- still says when and by whom. The channels themselves are in store_channel.
CREATE TABLE IF NOT EXISTS stores.store_channel_set (
    store_id SMALLINT NOT NULL,
    set_by UUID NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT store_channel_set_pkey PRIMARY KEY (store_id),
    CONSTRAINT store_channel_set_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES public.store(store_id) ON DELETE CASCADE,
    CONSTRAINT store_channel_set_set_by_fkey FOREIGN KEY (set_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT
);

-- stores holds no privilege on public.channel (sales is its read owner,
-- E-10), so an unknown channel id is refused by this foreign key.
CREATE TABLE IF NOT EXISTS stores.store_channel (
    store_id SMALLINT NOT NULL,
    channel_id SMALLINT NOT NULL,
    CONSTRAINT store_channel_pkey PRIMARY KEY (store_id, channel_id),
    CONSTRAINT store_channel_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES stores.store_channel_set(store_id) ON DELETE CASCADE,
    CONSTRAINT store_channel_channel_id_fkey FOREIGN KEY (channel_id)
        REFERENCES public.channel(channel_id) ON DELETE RESTRICT
);

-- max_discount_percent is NULL when the cap was removed: the store then
-- imposes no limit of its own, the same state as a store never written.
CREATE TABLE IF NOT EXISTS stores.store_commercial_rule (
    store_id SMALLINT NOT NULL,
    max_discount_percent NUMERIC(5,2),
    set_by UUID NOT NULL,
    set_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT store_commercial_rule_pkey PRIMARY KEY (store_id),
    CONSTRAINT store_commercial_rule_store_id_fkey FOREIGN KEY (store_id)
        REFERENCES public.store(store_id) ON DELETE CASCADE,
    CONSTRAINT store_commercial_rule_max_discount_percent_check
        CHECK (max_discount_percent BETWEEN 0 AND 100),
    CONSTRAINT store_commercial_rule_set_by_fkey FOREIGN KEY (set_by)
        REFERENCES public.app_user(user_id) ON DELETE RESTRICT
);

-- time_zone is NULL exactly when the store has no interval. Deferred, so one
-- transaction may replace the header and its intervals in any order.
CREATE OR REPLACE FUNCTION stores.check_hours_consistent() RETURNS trigger
LANGUAGE plpgsql
AS $check$
DECLARE
    target smallint;
    zone text;
    present boolean;
BEGIN
    target := COALESCE(NEW.store_id, OLD.store_id);
    SELECT h.time_zone, true INTO zone, present
    FROM stores.store_hours AS h WHERE h.store_id = target;
    IF present AND (zone IS NULL) = EXISTS (
        SELECT FROM stores.store_hours_interval AS i WHERE i.store_id = target
    ) THEN
        RAISE EXCEPTION 'store % hours: time_zone is null exactly when there is no interval', target
            USING ERRCODE = '23514', CONSTRAINT = 'store_hours_time_zone_consistent';
    END IF;
    RETURN NULL;
END
$check$;

CREATE TABLE IF NOT EXISTS stores_events.outbox (
    event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    stream TEXT NOT NULL,
    event_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    relayed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON stores_events.outbox (occurred_at) WHERE relayed_at IS NULL;

CREATE OR REPLACE VIEW api_stores.store_directory_v1
WITH (security_invoker = false) AS
SELECT s.store_id, s.name, s.city, s.state, s.is_active,
       l.latitude, l.longitude, l.geofence_radius_m
FROM public.store AS s
LEFT JOIN stores.store_location AS l USING (store_id);

-- v2 adds the facts of F21-12 and keeps v1's eight columns (contracts/db/
-- api_stores.store_directory.v2.md). The facts are published, never decided
-- here: a consumer applies "is_active, OPEN and the channel enabled".
CREATE OR REPLACE VIEW api_stores.store_directory_v2
WITH (security_invoker = false) AS
SELECT s.store_id, s.name, s.city, s.state, s.is_active,
       l.latitude, l.longitude, l.geofence_radius_m,
       COALESCE(st.status, 'OPEN')::text AS status,
       h.time_zone,
       COALESCE((
           SELECT jsonb_agg(jsonb_build_object(
                      'day_of_week', i.day_of_week,
                      'opens_at', to_char(i.opens_at, 'HH24:MI'),
                      'closes_at', to_char(i.closes_at, 'HH24:MI'))
                  ORDER BY i.day_of_week)
           FROM stores.store_hours_interval AS i WHERE i.store_id = s.store_id
       ), '[]'::jsonb) AS opening_hours,
       COALESCE((
           SELECT array_agg(c.channel_id ORDER BY c.channel_id)
           FROM stores.store_channel AS c WHERE c.store_id = s.store_id
       ), ARRAY[]::smallint[]) AS channel_ids,
       r.max_discount_percent
FROM public.store AS s
LEFT JOIN stores.store_location AS l USING (store_id)
LEFT JOIN stores.store_status AS st USING (store_id)
LEFT JOIN stores.store_hours AS h USING (store_id)
LEFT JOIN stores.store_commercial_rule AS r USING (store_id);

CREATE OR REPLACE FUNCTION stores_events.capture_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN entity := OLD.store_id::text;
    ELSE entity := NEW.store_id::text;
    END IF;
    INSERT INTO stores_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:stores.v1.changed', TG_OP, entity);
    PERFORM pg_notify('stores_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION stores_events.capture_changed() OWNER TO definer_stores;
REVOKE EXECUTE ON FUNCTION stores_events.capture_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_capture_stores_changed
AFTER INSERT OR UPDATE OR DELETE ON public.store
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_location_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_location
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_audit_store_location
AFTER INSERT OR UPDATE OR DELETE ON stores.store_location
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');

-- The new tables are captured and audited like the location. A child table
-- (an interval, an enabled channel) is captured too, so a write by any
-- writer is announced; the service writes only the rows that change.
CREATE OR REPLACE TRIGGER trg_capture_store_status_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_status
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_hours_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_hours_interval_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours_interval
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_channel_set_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_channel_set
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_channel_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_channel
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();
CREATE OR REPLACE TRIGGER trg_capture_store_commercial_rule_changed
AFTER INSERT OR UPDATE OR DELETE ON stores.store_commercial_rule
FOR EACH ROW EXECUTE FUNCTION stores_events.capture_changed();

CREATE OR REPLACE TRIGGER trg_audit_store_status
AFTER INSERT OR UPDATE OR DELETE ON stores.store_status
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');
CREATE OR REPLACE TRIGGER trg_audit_store_hours
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');
CREATE OR REPLACE TRIGGER trg_audit_store_hours_interval
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours_interval
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');
CREATE OR REPLACE TRIGGER trg_audit_store_channel_set
AFTER INSERT OR UPDATE OR DELETE ON stores.store_channel_set
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');
CREATE OR REPLACE TRIGGER trg_audit_store_channel
AFTER INSERT OR UPDATE OR DELETE ON stores.store_channel
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');
CREATE OR REPLACE TRIGGER trg_audit_store_commercial_rule
AFTER INSERT OR UPDATE OR DELETE ON stores.store_commercial_rule
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('store_id');

DROP TRIGGER IF EXISTS trg_store_hours_consistent ON stores.store_hours;
CREATE CONSTRAINT TRIGGER trg_store_hours_consistent
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stores.check_hours_consistent();
DROP TRIGGER IF EXISTS trg_store_hours_interval_consistent ON stores.store_hours_interval;
CREATE CONSTRAINT TRIGGER trg_store_hours_interval_consistent
AFTER INSERT OR UPDATE OR DELETE ON stores.store_hours_interval
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION stores.check_hours_consistent();

GRANT USAGE ON SCHEMA stores TO svc_stores;
GRANT SELECT, INSERT, UPDATE, DELETE ON stores.store_location TO svc_stores;
GRANT SELECT, INSERT, UPDATE, DELETE ON stores.store_status, stores.store_hours,
    stores.store_hours_interval, stores.store_channel_set, stores.store_channel,
    stores.store_commercial_rule TO svc_stores;
GRANT SELECT ON public.store TO svc_stores;
GRANT USAGE ON SCHEMA stores_events TO svc_stores, definer_stores;
GRANT INSERT ON stores_events.outbox TO definer_stores;
GRANT SELECT, DELETE ON stores_events.outbox TO svc_stores;
GRANT UPDATE (relayed_at) ON stores_events.outbox TO svc_stores;
GRANT USAGE ON SCHEMA api_stores TO svc_stores, svc_checkins, svc_engagement,
    svc_stock, svc_campaigns, svc_sales;
GRANT SELECT ON api_stores.store_directory_v1 TO svc_stores, svc_checkins, svc_engagement;
GRANT SELECT ON api_stores.store_directory_v2 TO svc_stores, svc_stock, svc_campaigns, svc_sales;
-- END BOUNDARY stores

-- BEGIN BOUNDARY users
-- users-service (F14-04, ADR-0034, ADR-0036, ADR-0044): the single writer of
-- accounts besides the frozen web (E-09). It publishes the credential view
-- auth reads (E-15) and captures every access change, the web's included, so
-- auth can end the user's sessions. Idempotent: an operator re-applies it.

CREATE SCHEMA IF NOT EXISTS api_users;
CREATE SCHEMA IF NOT EXISTS users;
CREATE SCHEMA IF NOT EXISTS users_events;
REVOKE ALL ON SCHEMA api_users FROM PUBLIC;
REVOKE ALL ON SCHEMA users FROM PUBLIC;
REVOKE ALL ON SCHEMA users_events FROM PUBLIC;

-- The stores at which a floor user may work. Deactivation and role changes
-- retain this history; the published view below decides whether it currently
-- grants anything. The store FK is deliberately checked by PostgreSQL: its
-- owner may read public.store, while svc_users receives no such privilege.
CREATE TABLE IF NOT EXISTS users.store_assignment (
    user_id  UUID NOT NULL,
    store_id SMALLINT NOT NULL,
    PRIMARY KEY (user_id, store_id)
);
CREATE INDEX IF NOT EXISTS idx_store_assignment_store
    ON users.store_assignment (store_id);
DO $$ BEGIN
    ALTER TABLE users.store_assignment ADD CONSTRAINT store_assignment_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.app_user(user_id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE users.store_assignment ADD CONSTRAINT store_assignment_store_id_fkey
        FOREIGN KEY (store_id) REFERENCES public.store(store_id) ON DELETE RESTRICT;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE OR REPLACE TRIGGER trg_audit_store_assignment
AFTER INSERT OR UPDATE OR DELETE ON users.store_assignment
FOR EACH ROW EXECUTE FUNCTION public.fn_audit('user_id');

-- Keys only, never state: consumers re-read the credential view.
CREATE TABLE IF NOT EXISTS users_events.outbox (
    event_id    uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    stream      text NOT NULL,
    event_type  text NOT NULL,
    entity_id   text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now(),
    relayed_at  timestamptz
);
CREATE INDEX IF NOT EXISTS outbox_unrelayed
    ON users_events.outbox (occurred_at) WHERE relayed_at IS NULL;

-- The only cross-boundary contract that carries a credential
-- (contracts/db/api_users.credential.v1.md). It runs as its owner, so auth
-- reads it without any privilege on app_user or role.
CREATE OR REPLACE VIEW api_users.credential_v1
WITH (security_invoker = off) AS
SELECT u.user_id,
       lower(u.email) AS email_lower,
       u.password_hash,
       u.is_active,
       r.code AS role_code
  FROM public.app_user AS u
  JOIN public.role AS r ON r.role_id = u.role_id;

-- Consumers re-read this view for every narrowed request (ADR-0060). Stored
-- rows survive deactivation and a non-floor role change, but grant no access.
CREATE OR REPLACE VIEW api_users.store_assignment_v1
WITH (security_invoker = off) AS
SELECT a.user_id, a.store_id
  FROM users.store_assignment AS a
  JOIN public.app_user AS u ON u.user_id = a.user_id
  JOIN public.role AS r ON r.role_id = u.role_id
 WHERE u.is_active
   AND r.code IN ('SALES_ASSOCIATE', 'STORE_MANAGER', 'LOYALTY_OFFICER',
                  'COMMERCIAL_SUPERVISOR');

-- One outbox row per access change, whichever role made it, in the writer's
-- transaction. A change of name or email alone is not an access change.
CREATE OR REPLACE FUNCTION users_events.capture_access_changed() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $capture$
DECLARE
    entity text;
BEGIN
    IF TG_OP = 'DELETE' THEN
        entity := OLD.user_id::text;
    ELSE
        entity := NEW.user_id::text;
    END IF;
    INSERT INTO users_events.outbox (stream, event_type, entity_id)
    VALUES ('stream:users.v1.access_changed', TG_OP, entity);
    -- An empty wake-up for the users relay; only that relay LISTENs.
    PERFORM pg_notify('users_outbox', '');
    RETURN NULL;
END
$capture$;
ALTER FUNCTION users_events.capture_access_changed() OWNER TO definer_users;
REVOKE EXECUTE ON FUNCTION users_events.capture_access_changed() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_users_capture_access_changed
AFTER UPDATE OF is_active, role_id, password_hash OR DELETE ON public.app_user
FOR EACH ROW EXECUTE FUNCTION users_events.capture_access_changed();

-- svc_users writes accounts and never deletes one (RN-04).
GRANT SELECT, INSERT ON public.app_user TO svc_users;
GRANT UPDATE (name, email, role_id, is_active, password_hash) ON public.app_user TO svc_users;
GRANT SELECT ON public.role TO svc_users;

GRANT USAGE ON SCHEMA users TO svc_users;
GRANT SELECT, INSERT, DELETE ON users.store_assignment TO svc_users;

GRANT USAGE ON SCHEMA users_events TO svc_users, definer_users;
GRANT INSERT ON users_events.outbox TO definer_users;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON users_events.outbox TO svc_users;

GRANT USAGE ON SCHEMA api_users TO svc_users, svc_auth, svc_sales, svc_checkins, svc_engagement;
GRANT SELECT ON api_users.credential_v1 TO svc_users, svc_auth;
GRANT SELECT ON api_users.store_assignment_v1
    TO svc_users, svc_sales, svc_checkins, svc_engagement;
-- END BOUNDARY users

-- BEGIN BOUNDARY check-ins
-- check-ins-service (F14-26, ADR-0046): GPS check-ins whose verdict the server
-- decides (RN-56), and the append-only reviews that override it. No outbox:
-- nothing consumes these. The web has no privilege here; a store or user with
-- check-ins can no longer be deleted (E-11).
CREATE SCHEMA IF NOT EXISTS checkins;
REVOKE ALL ON SCHEMA checkins FROM PUBLIC;

-- No verified column: it would depend on reason, or on the latest review.
-- distance_m and reason are fixed at recorded_at against the location then.
CREATE TABLE IF NOT EXISTS checkins.store_check_in (
    check_in_id UUID NOT NULL DEFAULT gen_random_uuid(),
    client_check_in_id UUID NOT NULL,
    user_id UUID NOT NULL,
    store_id SMALLINT NOT NULL,
    latitude NUMERIC,
    longitude NUMERIC,
    accuracy_m NUMERIC,
    distance_m NUMERIC,
    reason TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every named constraint is added separately so this boundary is safe to
-- re-apply to a database that already contains the table.
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_pkey'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_pkey
        PRIMARY KEY (check_in_id); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_client_check_in_id_key'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_client_check_in_id_key
        UNIQUE (client_check_in_id); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_user_id_fkey'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_user_id_fkey
        FOREIGN KEY (user_id) REFERENCES public.app_user(user_id) ON DELETE RESTRICT; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_store_id_fkey'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_store_id_fkey
        FOREIGN KEY (store_id) REFERENCES public.store(store_id) ON DELETE RESTRICT; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_reason_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_reason_check
        CHECK (reason IN ('within_geofence', 'outside_geofence', 'low_accuracy',
                          'store_location_unknown', 'no_fix')); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_latitude_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_latitude_check
        CHECK (latitude BETWEEN -90 AND 90); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_longitude_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_longitude_check
        CHECK (longitude BETWEEN -180 AND 180); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_accuracy_m_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_accuracy_m_check
        CHECK (accuracy_m > 0); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_fix_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_fix_check
        CHECK (CASE WHEN reason = 'no_fix'
               THEN num_nulls(latitude, longitude, accuracy_m) = 3
               ELSE num_nulls(latitude, longitude, accuracy_m) = 0 END); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.store_check_in'::regclass
      AND conname = 'store_check_in_distance_check'
) THEN ALTER TABLE checkins.store_check_in ADD CONSTRAINT store_check_in_distance_check
        CHECK ((distance_m IS NULL) = (reason IN ('no_fix', 'store_location_unknown'))); END IF; END $$;

-- review_id is a number because the v1 contract publishes it as int64.
CREATE TABLE IF NOT EXISTS checkins.check_in_review (
    review_id BIGINT GENERATED ALWAYS AS IDENTITY,
    check_in_id UUID NOT NULL,
    verified BOOLEAN NOT NULL,
    reason TEXT NOT NULL,
    reviewed_by UUID NOT NULL,
    reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.check_in_review'::regclass
      AND conname = 'check_in_review_pkey'
) THEN ALTER TABLE checkins.check_in_review ADD CONSTRAINT check_in_review_pkey
        PRIMARY KEY (review_id); END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.check_in_review'::regclass
      AND conname = 'check_in_review_check_in_id_fkey'
) THEN ALTER TABLE checkins.check_in_review ADD CONSTRAINT check_in_review_check_in_id_fkey
        FOREIGN KEY (check_in_id) REFERENCES checkins.store_check_in(check_in_id)
        ON DELETE RESTRICT; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.check_in_review'::regclass
      AND conname = 'check_in_review_reviewed_by_fkey'
) THEN ALTER TABLE checkins.check_in_review ADD CONSTRAINT check_in_review_reviewed_by_fkey
        FOREIGN KEY (reviewed_by) REFERENCES public.app_user(user_id) ON DELETE RESTRICT; END IF; END $$;
DO $$ BEGIN IF NOT EXISTS (
    SELECT FROM pg_constraint WHERE conrelid = 'checkins.check_in_review'::regclass
      AND conname = 'check_in_review_reason_check'
) THEN ALTER TABLE checkins.check_in_review ADD CONSTRAINT check_in_review_reason_check
        CHECK (btrim(reason) <> ''); END IF; END $$;

CREATE INDEX IF NOT EXISTS store_check_in_store_occurred
    ON checkins.store_check_in (store_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS store_check_in_user_occurred
    ON checkins.store_check_in (user_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS check_in_review_check_in_reviewed
    ON checkins.check_in_review (check_in_id, reviewed_at);

-- Append-only: SELECT and INSERT, never UPDATE or DELETE (RN-56). The store
-- locations come from api_stores.store_directory_v1, granted in the stores
-- section.
GRANT USAGE ON SCHEMA checkins TO svc_checkins;
GRANT SELECT, INSERT ON checkins.store_check_in, checkins.check_in_review TO svc_checkins;
-- END BOUNDARY check-ins

-- BEGIN BOUNDARY campaigns
-- campaigns-service (F14-28, ADR-0034, ADR-0045): the campaign state machine,
-- ported from web/services/campaigns.py and pinned to it by golden fixtures.
-- It adds no table and no api_ object. The table, its seed and its audit
-- trigger stay as they are, and the web remains a legacy co-writer (E-09).
-- Idempotent: an operator re-applies it.

-- E-13: RN-24 reads the web-only experiment tables, which no service may
-- read. A service session (session_user, which SET ROLE cannot change) that
-- activates a campaign is refused while an attached experiment is not ready:
-- no treatment group, or no control and a treatment count other than two.
-- This is the test of activation_refusal in web/services/experiments.py. It
-- does not fire for the web's own sessions, which run that check themselves.
-- The function runs as definer_guard, whose only privilege on these tables is
-- SELECT, so svc_campaigns gains none. The refusal is a check_violation under
-- its own constraint name, which the service maps to 409.
CREATE OR REPLACE FUNCTION fn_campaign_activation_guard() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $guard$
BEGIN
    IF EXISTS (
        SELECT FROM public.experiment AS e
        WHERE e.campaign_id = NEW.campaign_id
          AND (
              NOT EXISTS (
                  SELECT FROM public.experiment_group AS g
                  WHERE g.experiment_id = e.experiment_id AND g.kind = 'TREATMENT'
              )
              OR (
                  NOT EXISTS (
                      SELECT FROM public.experiment_group AS g
                      WHERE g.experiment_id = e.experiment_id AND g.kind = 'CONTROL'
                  )
                  AND (
                      SELECT count(*) FROM public.experiment_group AS g
                      WHERE g.experiment_id = e.experiment_id AND g.kind = 'TREATMENT'
                  ) <> 2
              )
          )
    ) THEN
        RAISE EXCEPTION 'campaign % has an experiment that is not ready', NEW.campaign_id
            USING ERRCODE = 'check_violation',
                  CONSTRAINT = 'experiment_not_ready';
    END IF;
    RETURN NEW;
END
$guard$;
ALTER FUNCTION fn_campaign_activation_guard() OWNER TO definer_guard;
REVOKE EXECUTE ON FUNCTION fn_campaign_activation_guard() FROM PUBLIC;

CREATE OR REPLACE TRIGGER trg_campaign_activation_guard
BEFORE UPDATE OF status ON public.campaign
FOR EACH ROW
WHEN (NEW.status = 'ACTIVE' AND session_user = 'svc_campaigns')
EXECUTE FUNCTION fn_campaign_activation_guard();

GRANT SELECT ON public.experiment, public.experiment_group TO definer_guard;

-- svc_campaigns writes campaigns and never deletes one (RN-15). The label
-- foreign key into segment_label is checked as the parent's owner (E-11), so
-- it needs no privilege there.
GRANT SELECT, INSERT, UPDATE ON public.campaign TO svc_campaigns;
-- END BOUNDARY campaigns

-- BEGIN BOUNDARY audit-trail
-- audit-trail-service (F14-30, ADR-0034): reads audit_log, and owns no write
-- on it: fn_audit, which runs as definer_audit, is the only writer of an entry
-- (E-12), and no svc_* role holds INSERT, UPDATE, DELETE or TRUNCATE on the
-- table. Only this role reads it. Idempotent: an operator re-applies it.
GRANT SELECT ON public.audit_log TO svc_audit_trail;

-- Domain events (F21-25, ADR-0072): calculations, model publications and
-- rollbacks, permission changes, critical actions and logged reads, recorded
-- by their producers through api_audit.record_event_v1 inside the transaction
-- of the act. The table is audit-trail's; svc_audit_trail only reads it, and
-- definer_audit only appends. Contract:
-- contracts/db/api_audit.record_event.v1.md.
CREATE SCHEMA IF NOT EXISTS audit_events;
REVOKE ALL ON SCHEMA audit_events FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS api_audit;
REVOKE ALL ON SCHEMA api_audit FROM PUBLIC;

-- seq is the chain position, assigned under advisory lock 272001 so it is
-- gapless and the chain has one head. Nothing references app_user, customer
-- or store: an event is evidence and outlives what it names.
CREATE TABLE IF NOT EXISTS audit_events.domain_event (
    seq              bigint      NOT NULL,
    event_id         uuid        PRIMARY KEY,
    kind             text        NOT NULL,
    outcome          text        NOT NULL,
    occurred_at      timestamptz NOT NULL,
    recorded_at      timestamptz NOT NULL,
    actor_type       text        NOT NULL,
    actor_user_id    uuid,
    actor_role       text,
    source           text        NOT NULL,
    store_id         smallint,
    subject_type     text,
    subject_id       text,
    model_version_id bigint,
    rule_set_version bigint,
    correlation_id   text,
    details          jsonb       NOT NULL,
    prev_hash        text        NOT NULL,
    entry_hash       text        NOT NULL,
    CONSTRAINT domain_event_seq_key UNIQUE (seq),
    CONSTRAINT domain_event_entry_hash_key UNIQUE (entry_hash),
    CONSTRAINT domain_event_seq_positive CHECK (seq > 0),
    CONSTRAINT domain_event_kind CHECK (kind IN (
        'CALCULATION_RUN', 'MODEL_PUBLISHED', 'MODEL_ROLLED_BACK',
        'PERMISSION_CHANGED', 'CRITICAL_ACTION', 'HISTORY_READ', 'AUDIT_READ')),
    CONSTRAINT domain_event_outcome CHECK (outcome IN ('SUCCESS', 'REFUSED')),
    CONSTRAINT domain_event_actor CHECK (
        (actor_type = 'USER' AND actor_user_id IS NOT NULL AND actor_role IS NOT NULL)
        OR (actor_type = 'SYSTEM' AND actor_user_id IS NULL AND actor_role IS NULL)),
    CONSTRAINT domain_event_subject CHECK ((subject_type IS NULL) = (subject_id IS NULL)),
    CONSTRAINT domain_event_details_object CHECK (jsonb_typeof(details) = 'object'),
    CONSTRAINT domain_event_hash_format CHECK (
        prev_hash ~ '^[0-9a-f]{64}$' AND entry_hash ~ '^[0-9a-f]{64}$')
);
CREATE INDEX IF NOT EXISTS domain_event_recorded
    ON audit_events.domain_event (recorded_at DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS domain_event_kind_recorded
    ON audit_events.domain_event (kind, recorded_at DESC);
CREATE INDEX IF NOT EXISTS domain_event_actor_user
    ON audit_events.domain_event (actor_user_id) WHERE actor_user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS domain_event_model_version
    ON audit_events.domain_event (model_version_id) WHERE model_version_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS domain_event_subject_key
    ON audit_events.domain_event (subject_type, subject_id) WHERE subject_type IS NOT NULL;

-- Second layer after the privileges (ADR-0072 section 6): even the owner's
-- UPDATE, DELETE or TRUNCATE is refused unless the operator's archiving script
-- drops these triggers first.
CREATE OR REPLACE FUNCTION audit_events.refuse_change() RETURNS trigger
LANGUAGE plpgsql
SET search_path = pg_catalog, pg_temp
AS $refuse$
BEGIN
    RAISE EXCEPTION 'domain events are append-only (% refused)', TG_OP
        USING ERRCODE = 'check_violation', CONSTRAINT = 'domain_event_append_only';
END
$refuse$;
CREATE OR REPLACE TRIGGER domain_event_append_only
BEFORE UPDATE OR DELETE ON audit_events.domain_event
FOR EACH ROW EXECUTE FUNCTION audit_events.refuse_change();
CREATE OR REPLACE TRIGGER domain_event_no_truncate
BEFORE TRUNCATE ON audit_events.domain_event
FOR EACH STATEMENT EXECUTE FUNCTION audit_events.refuse_change();

-- The canonical form: the jsonb text of every column but entry_hash, with
-- timestamps in UTC whatever the session's TimeZone. audit-trail recomputes
-- it to verify the chain.
CREATE OR REPLACE FUNCTION audit_events.entry_hash_v1(e audit_events.domain_event)
RETURNS text
LANGUAGE sql
STABLE
SET search_path = pg_catalog, pg_temp
SET timezone = 'UTC'
AS $hash$
    SELECT encode(sha256(convert_to((to_jsonb(e) - 'entry_hash')::text, 'UTF8')), 'hex')
$hash$;

-- RN-29: a key naming a credential, or a string shaped like a token or a
-- password hash, anywhere in a value.
CREATE OR REPLACE FUNCTION audit_events.credential_text(t text) RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $cred$
    SELECT t IS NOT NULL AND (
        t ~ '^[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$'
        OR t ~ '(^|[^A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{4,}\.[A-Za-z0-9_-]{4,}\.'
        OR t ~ '\$argon2(id|i|d)\$'
        OR t ~ '\$2[aby]\$[0-9]{2}\$')
$cred$;

CREATE OR REPLACE FUNCTION audit_events.credential_found(j jsonb) RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $found$
DECLARE
    k text;
    v jsonb;
BEGIN
    CASE jsonb_typeof(j)
        WHEN 'object' THEN
            FOR k, v IN SELECT key, value FROM jsonb_each(j) LOOP
                IF k ~* '(password|hash|secret|token|key|credential)'
                   OR audit_events.credential_found(v) THEN
                    RETURN true;
                END IF;
            END LOOP;
        WHEN 'array' THEN
            FOR v IN SELECT value FROM jsonb_array_elements(j) LOOP
                IF audit_events.credential_found(v) THEN
                    RETURN true;
                END IF;
            END LOOP;
        WHEN 'string' THEN
            RETURN audit_events.credential_text(j #>> '{}');
        ELSE
            NULL;
    END CASE;
    RETURN false;
END
$found$;

-- One key's value: a string in a closed set, a string matching a pattern, a
-- non-negative integer, or an array of distinct members of a closed set.
CREATE OR REPLACE FUNCTION audit_events.str_in(d jsonb, k text, allowed text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce(jsonb_typeof(d -> k) = 'string' AND (d ->> k) = ANY (allowed), false) $$;

CREATE OR REPLACE FUNCTION audit_events.str_like(d jsonb, k text, pattern text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce(jsonb_typeof(d -> k) = 'string' AND (d ->> k) ~ pattern, false) $$;

CREATE OR REPLACE FUNCTION audit_events.count_ok(d jsonb, k text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce(jsonb_typeof(d -> k) = 'number' AND (d ->> k) ~ '^[0-9]{1,18}$', false) $$;

-- Whether details carries exactly the keys a kind allows, with values of the
-- right type and set. Values are scalars, or flat arrays where allowed.
CREATE OR REPLACE FUNCTION audit_events.details_valid(p_kind text, d jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SET search_path = pg_catalog, pg_temp
AS $valid$
DECLARE
    required text[];
    optional text[];
    role_code constant text := '^[A-Z][A-Z_]{1,29}$';
    reason constant text := '^[A-Z][A-Z0-9_]{0,39}$';
    stamp constant text :=
        '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(\.[0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$';
    change text;
BEGIN
    IF d IS NULL OR jsonb_typeof(d) <> 'object' THEN
        RETURN false;
    END IF;
    CASE p_kind
        WHEN 'CALCULATION_RUN' THEN
            required := ARRAY['calculation', 'status', 'input_count', 'output_count'];
            optional := ARRAY['parameters_digest', 'window_from', 'window_to', 'data_origin'];
        WHEN 'MODEL_PUBLISHED', 'MODEL_ROLLED_BACK' THEN
            required := ARRAY['previous_version_id', 'reason_code'];
            optional := ARRAY[]::text[];
        WHEN 'PERMISSION_CHANGED' THEN
            required := ARRAY['change'];
            optional := ARRAY['from_role', 'to_role', 'store_ids'];
        WHEN 'CRITICAL_ACTION' THEN
            required := ARRAY['action'];
            optional := ARRAY['reason_code'];
        WHEN 'HISTORY_READ' THEN
            required := ARRAY['history'];
            optional := ARRAY[]::text[];
        WHEN 'AUDIT_READ' THEN
            required := ARRAY['resource', 'filters'];
            optional := ARRAY[]::text[];
        ELSE
            RETURN false;
    END CASE;
    IF NOT (d ?& required)
       OR EXISTS (SELECT 1 FROM jsonb_object_keys(d) AS key
                  WHERE key <> ALL (required || optional)) THEN
        RETURN false;
    END IF;

    CASE p_kind
        WHEN 'CALCULATION_RUN' THEN
            RETURN audit_events.str_in(d, 'calculation', ARRAY['RFM', 'SEGMENTATION', 'KPI'])
               AND audit_events.str_in(d, 'status', ARRAY['COMPLETED', 'FAILED'])
               AND audit_events.count_ok(d, 'input_count')
               AND audit_events.count_ok(d, 'output_count')
               AND (NOT d ? 'parameters_digest' OR audit_events.str_like(d, 'parameters_digest', '^[0-9a-f]{64}$'))
               AND (NOT d ? 'window_from' OR audit_events.str_like(d, 'window_from', stamp))
               AND (NOT d ? 'window_to' OR audit_events.str_like(d, 'window_to', stamp))
               AND (NOT d ? 'data_origin' OR audit_events.str_in(d, 'data_origin', ARRAY['OBSERVED', 'SEEDED']));
        WHEN 'MODEL_PUBLISHED', 'MODEL_ROLLED_BACK' THEN
            RETURN (jsonb_typeof(d -> 'previous_version_id') = 'null'
                    OR (jsonb_typeof(d -> 'previous_version_id') = 'number'
                        AND (d ->> 'previous_version_id') ~ '^[1-9][0-9]{0,17}$'))
               AND audit_events.str_like(d, 'reason_code', reason);
        WHEN 'PERMISSION_CHANGED' THEN
            IF NOT audit_events.str_in(d, 'change', ARRAY[
                    'ROLE_ASSIGNED', 'ADMINISTRATOR_TRANSFERRED', 'USER_DEACTIVATED',
                    'USER_REACTIVATED', 'STORES_ASSIGNED', 'STORES_UNASSIGNED']) THEN
                RETURN false;
            END IF;
            change := d ->> 'change';
            IF change IN ('ROLE_ASSIGNED', 'ADMINISTRATOR_TRANSFERRED') THEN
                RETURN NOT d ? 'store_ids'
                   AND audit_events.str_like(d, 'from_role', role_code)
                   AND audit_events.str_like(d, 'to_role', role_code)
                   AND d ->> 'from_role' <> d ->> 'to_role';
            ELSIF change IN ('STORES_ASSIGNED', 'STORES_UNASSIGNED') THEN
                RETURN NOT d ? 'from_role' AND NOT d ? 'to_role'
                   AND jsonb_typeof(d -> 'store_ids') = 'array'
                   AND jsonb_array_length(d -> 'store_ids') > 0
                   AND NOT EXISTS (
                       SELECT 1 FROM jsonb_array_elements(d -> 'store_ids') AS s(v)
                       WHERE jsonb_typeof(s.v) <> 'number'
                          OR (s.v #>> '{}') !~ '^[1-9][0-9]{0,4}$'
                          OR (s.v #>> '{}')::int > 32767)
                   AND (SELECT count(DISTINCT v) = count(*)
                          FROM jsonb_array_elements(d -> 'store_ids') AS s(v));
            ELSE
                RETURN NOT d ? 'from_role' AND NOT d ? 'to_role' AND NOT d ? 'store_ids';
            END IF;
        WHEN 'CRITICAL_ACTION' THEN
            RETURN audit_events.str_in(d, 'action', ARRAY[
                    'SALE_REVERSED', 'STOCK_ADJUSTED', 'CUSTOMER_ANONYMIZED',
                    'AUDIT_PAYLOAD_REDACTED', 'EXPERIMENT_FROZEN', 'CAMPAIGN_ACTIVATED'])
               AND (NOT d ? 'reason_code' OR audit_events.str_like(d, 'reason_code', reason));
        WHEN 'HISTORY_READ' THEN
            RETURN audit_events.str_in(d, 'history', ARRAY['CONSENT', 'SEGMENT', 'SALES']);
        WHEN 'AUDIT_READ' THEN
            RETURN audit_events.str_in(d, 'resource', ARRAY[
                    'AUDIT_ENTRIES', 'DOMAIN_EVENTS', 'DOMAIN_EVENT_VERIFICATION'])
               AND jsonb_typeof(d -> 'filters') = 'array'
               AND NOT EXISTS (
                   SELECT 1 FROM jsonb_array_elements(d -> 'filters') AS f(v)
                   WHERE jsonb_typeof(f.v) <> 'string'
                      OR (f.v #>> '{}') <> ALL (ARRAY[
                          'entity', 'entity_pk', 'user_id', 'action', 'kind',
                          'actor_user_id', 'actor_role', 'model_version_id',
                          'subject_type', 'subject_id', 'store_id', 'outcome',
                          'source', 'from', 'to', 'page', 'size']))
               AND (SELECT count(DISTINCT v) = count(*)
                      FROM jsonb_array_elements(d -> 'filters') AS f(v));
    END CASE;
    RETURN false;
END
$valid$;

-- The checks and the append. p_source is the recording role: record_event_v1
-- passes session_user, and only the seed, run by the owner, names one. No
-- svc_* role may call this directly.
CREATE OR REPLACE FUNCTION audit_events.append_v1(
    p_source text,
    p_event_id uuid,
    p_kind text,
    p_outcome text,
    p_occurred_at timestamptz,
    p_actor_type text,
    p_actor_user_id uuid,
    p_actor_role text,
    p_store_id smallint,
    p_subject_type text,
    p_subject_id text,
    p_model_version_id bigint,
    p_rule_set_version bigint,
    p_correlation_id text,
    p_details jsonb
) RETURNS TABLE (recorded_at timestamptz, entry_hash text, created boolean)
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $append$
#variable_conflict use_column
DECLARE
    writers text[];
    stored audit_events.domain_event;
    r audit_events.domain_event;
    subjects text[] := ARRAY[
        'user', 'customer', 'model_version', 'calculation', 'sale', 'sale_reversal',
        'stock_level', 'campaign', 'experiment', 'audit_entry', 'domain_event'];
BEGIN
    -- 1. The kind is in the closed list and mapped to the recording role.
    writers := CASE p_kind
        WHEN 'CALCULATION_RUN' THEN ARRAY['svc_rfm', 'svc_segments', 'svc_kpis']
        WHEN 'MODEL_PUBLISHED' THEN ARRAY['svc_models']
        WHEN 'MODEL_ROLLED_BACK' THEN ARRAY['svc_models']
        WHEN 'PERMISSION_CHANGED' THEN ARRAY['svc_users']
        WHEN 'CRITICAL_ACTION' THEN ARRAY['svc_sales', 'svc_stock', 'svc_customers',
                                          'svc_experiments', 'svc_campaigns']
        WHEN 'HISTORY_READ' THEN ARRAY['svc_preferences', 'svc_segments', 'svc_sales']
        WHEN 'AUDIT_READ' THEN ARRAY['svc_audit_trail']
    END;
    IF writers IS NULL OR p_source IS NULL OR NOT (p_source = ANY (writers)) THEN
        RAISE EXCEPTION '% may not record kind %', p_source, coalesce(p_kind, 'NULL')
            USING ERRCODE = 'MA001', CONSTRAINT = 'event_kind_not_permitted';
    END IF;

    -- 2. No credential anywhere (RN-29), before the key check so a credential
    -- is always reported as one.
    IF audit_events.credential_found(p_details)
       OR audit_events.credential_text(p_subject_id)
       OR audit_events.credential_text(p_correlation_id) THEN
        RAISE EXCEPTION 'a domain event never carries a credential'
            USING ERRCODE = 'MA003', CONSTRAINT = 'event_credential_refused';
    END IF;

    -- 3. Exactly the keys the kind allows.
    IF NOT audit_events.details_valid(p_kind, p_details) THEN
        RAISE EXCEPTION 'details do not match kind %', p_kind
            USING ERRCODE = 'MA002', CONSTRAINT = 'event_details_invalid';
    END IF;

    -- 4. The common fields.
    IF p_event_id IS NULL
       OR p_outcome IS NULL OR p_outcome NOT IN ('SUCCESS', 'REFUSED')
       OR (p_outcome = 'REFUSED' AND p_kind <> 'HISTORY_READ')
       OR p_occurred_at IS NULL OR p_occurred_at > now() + interval '5 minutes'
       OR p_actor_type IS NULL OR p_actor_type NOT IN ('USER', 'SYSTEM')
       OR (p_actor_type = 'USER' AND (p_actor_user_id IS NULL OR p_actor_role IS NULL
                                      OR p_actor_role !~ '^[A-Z][A-Z_]{1,29}$'))
       OR (p_actor_type = 'SYSTEM' AND (p_actor_user_id IS NOT NULL OR p_actor_role IS NOT NULL))
       OR (p_kind = 'AUDIT_READ' AND p_actor_type <> 'USER')
       OR (p_subject_type IS NULL) <> (p_subject_id IS NULL)
       OR (p_subject_type IS NOT NULL AND p_subject_type <> ALL (subjects))
       OR (p_subject_id IS NOT NULL AND length(p_subject_id) NOT BETWEEN 1 AND 80)
       OR (p_kind = 'PERMISSION_CHANGED' AND p_subject_type IS DISTINCT FROM 'user')
       OR (p_kind = 'HISTORY_READ' AND p_subject_type IS DISTINCT FROM 'customer')
       OR (p_kind IN ('MODEL_PUBLISHED', 'MODEL_ROLLED_BACK')
           AND (p_subject_type IS DISTINCT FROM 'model_version' OR p_model_version_id IS NULL))
       OR (p_kind = 'CRITICAL_ACTION' AND p_subject_type IS NULL)
       OR (p_kind = 'CALCULATION_RUN' AND p_subject_type IS NOT NULL
           AND p_subject_type <> 'calculation')
       OR (p_kind = 'AUDIT_READ' AND p_subject_type IS NOT NULL
           AND p_subject_type NOT IN ('domain_event', 'audit_entry'))
       OR (p_store_id IS NOT NULL AND p_store_id < 1)
       OR (p_model_version_id IS NOT NULL AND p_model_version_id < 1)
       OR (p_rule_set_version IS NOT NULL AND p_rule_set_version < 1)
       OR (p_correlation_id IS NOT NULL AND p_correlation_id !~ '^[0-9A-Za-z-]{1,64}$') THEN
        RAISE EXCEPTION 'the event''s common fields are invalid'
            USING ERRCODE = 'MA004', CONSTRAINT = 'event_invalid';
    END IF;

    -- One head: every writer appends under the same transaction lock
    -- (contracts/db/advisory-locks.md).
    PERFORM pg_advisory_xact_lock(272001);

    SELECT * INTO stored FROM audit_events.domain_event d WHERE d.event_id = p_event_id;
    IF FOUND THEN
        IF stored.source <> p_source OR stored.kind <> p_kind THEN
            RAISE EXCEPTION 'event % was recorded by another source or kind', p_event_id
                USING ERRCODE = 'MA005', CONSTRAINT = 'event_id_conflict';
        END IF;
        RETURN QUERY SELECT stored.recorded_at, stored.entry_hash, false;
        RETURN;
    END IF;

    SELECT coalesce(max(d.seq), 0) + 1 INTO r.seq FROM audit_events.domain_event d;
    SELECT coalesce((SELECT d.entry_hash FROM audit_events.domain_event d
                      ORDER BY d.seq DESC LIMIT 1), repeat('0', 64))
      INTO r.prev_hash;
    r.event_id := p_event_id;
    r.kind := p_kind;
    r.outcome := p_outcome;
    r.occurred_at := p_occurred_at;
    r.recorded_at := now();
    r.actor_type := p_actor_type;
    r.actor_user_id := p_actor_user_id;
    r.actor_role := p_actor_role;
    r.source := p_source;
    r.store_id := p_store_id;
    r.subject_type := p_subject_type;
    r.subject_id := p_subject_id;
    r.model_version_id := p_model_version_id;
    r.rule_set_version := p_rule_set_version;
    r.correlation_id := p_correlation_id;
    r.details := p_details;
    r.entry_hash := audit_events.entry_hash_v1(r);

    INSERT INTO audit_events.domain_event SELECT r.*;
    RETURN QUERY SELECT r.recorded_at, r.entry_hash, true;
END
$append$;
ALTER FUNCTION audit_events.append_v1(text, uuid, text, text, timestamptz, text, uuid, text,
    smallint, text, text, bigint, bigint, text, jsonb) OWNER TO definer_audit;
REVOKE EXECUTE ON FUNCTION audit_events.append_v1(text, uuid, text, text, timestamptz, text,
    uuid, text, smallint, text, text, bigint, bigint, text, jsonb) FROM PUBLIC;

-- The one writer a producer calls. source is session_user, which neither SET
-- ROLE nor this function's SECURITY DEFINER changes.
CREATE OR REPLACE FUNCTION api_audit.record_event_v1(
    p_event_id uuid,
    p_kind text,
    p_outcome text,
    p_occurred_at timestamptz,
    p_actor_type text,
    p_actor_user_id uuid,
    p_actor_role text,
    p_store_id smallint,
    p_subject_type text,
    p_subject_id text,
    p_model_version_id bigint,
    p_rule_set_version bigint,
    p_correlation_id text,
    p_details jsonb
) RETURNS TABLE (recorded_at timestamptz, entry_hash text, created boolean)
LANGUAGE sql
SECURITY DEFINER
SET search_path = public, pg_temp
AS $record$
    SELECT * FROM audit_events.append_v1(
        session_user::text, p_event_id, p_kind, p_outcome, p_occurred_at, p_actor_type,
        p_actor_user_id, p_actor_role, p_store_id, p_subject_type, p_subject_id,
        p_model_version_id, p_rule_set_version, p_correlation_id, p_details)
$record$;
ALTER FUNCTION api_audit.record_event_v1(uuid, text, text, timestamptz, text, uuid, text,
    smallint, text, text, bigint, bigint, text, jsonb) OWNER TO definer_audit;
REVOKE EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, timestamptz, text,
    uuid, text, smallint, text, text, bigint, bigint, text, jsonb) FROM PUBLIC;

REVOKE ALL ON audit_events.domain_event FROM PUBLIC;
GRANT USAGE ON SCHEMA audit_events TO definer_audit, svc_audit_trail;
GRANT SELECT, INSERT ON audit_events.domain_event TO definer_audit;
GRANT SELECT ON audit_events.domain_event TO svc_audit_trail;

-- The writers whose roles exist; svc_rfm, svc_models, svc_kpis and
-- svc_experiments receive theirs with the role.
GRANT USAGE ON SCHEMA api_audit TO svc_users, svc_sales, svc_segments, svc_preferences,
    svc_stock, svc_customers, svc_campaigns, svc_audit_trail;
GRANT EXECUTE ON FUNCTION api_audit.record_event_v1(uuid, text, text, timestamptz, text, uuid,
    text, smallint, text, text, bigint, bigint, text, jsonb)
    TO svc_users, svc_sales, svc_segments, svc_preferences, svc_stock, svc_customers,
       svc_campaigns, svc_audit_trail;
-- END BOUNDARY audit-trail

-- BEGIN APPLICATION ROLE VERIFICATION
-- F1-05 (#53). Opt-in acceptance checks, kept here because even a rejected
-- DROP belongs in the only file allowed to contain table DDL. Normal schema
-- creation skips this section. To verify an existing database, extract only
-- this section and connect as retail_app; see deploy/postgresql/README.md.
-- All rows and any unexpectedly successful DROP roll back. Audit sequence
-- values consumed by the probes are not reclaimed by PostgreSQL.
\if :{?verify_app_role}
BEGIN;
SET LOCAL lock_timeout = '2s';
SET LOCAL statement_timeout = '10s';
DO $verify$
DECLARE
    audit_before bigint;
BEGIN
    IF current_user <> 'retail_app' OR session_user <> 'retail_app' THEN
        RAISE EXCEPTION 'Connect directly as retail_app to verify its privileges';
    END IF;

    IF EXISTS (
        SELECT FROM pg_roles
        WHERE rolname = current_user
          AND (rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)
    ) OR EXISTS (
        SELECT FROM pg_roles
        WHERE rolname <> current_user AND pg_has_role(oid, 'MEMBER')
    ) THEN
        RAISE EXCEPTION 'Application role has administrative privileges or membership';
    END IF;

    IF has_database_privilege(current_database(), 'CREATE')
       OR has_schema_privilege('public', 'CREATE')
       OR EXISTS (
           SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
           WHERE n.nspname = 'public' AND pg_has_role(c.relowner, 'USAGE')
       ) THEN
        RAISE EXCEPTION 'Application role can create or owns persistent schema objects';
    END IF;

    IF EXISTS (
        SELECT FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relkind = 'r'
          AND c.relname NOT IN (
              'audit_log', 'experiment_assignment', 'experiment_exposure',
              'experiment_conversion', 'experiment', 'experiment_group', 'segmentation_run',
              'customer_segment_history'
          )
          AND (NOT has_table_privilege(c.oid, 'SELECT')
               OR NOT has_table_privilege(c.oid, 'INSERT')
               OR NOT has_table_privilege(c.oid, 'UPDATE')
               OR NOT has_table_privilege(c.oid, 'DELETE')
               OR has_table_privilege(c.oid, 'TRUNCATE'))
    ) THEN
        RAISE EXCEPTION 'Application table privileges differ from the DML-only policy';
    END IF;
    RAISE NOTICE 'PASS: restricted role; ordinary tables retain DML privileges';

    IF NOT has_table_privilege('public.audit_log', 'SELECT')
       OR NOT has_table_privilege('public.audit_log', 'INSERT')
       OR has_table_privilege('public.audit_log', 'UPDATE')
       OR has_table_privilege('public.audit_log', 'DELETE')
       OR has_table_privilege('public.audit_log', 'TRUNCATE') THEN
        RAISE EXCEPTION 'audit_log must be append-only for the application role (RN-30)';
    END IF;
    RAISE NOTICE 'PASS: audit_log is append-only for the application role';

    IF NOT has_table_privilege('public.experiment_assignment', 'SELECT')
       OR NOT has_table_privilege('public.experiment_assignment', 'INSERT')
       OR has_table_privilege('public.experiment_assignment', 'UPDATE')
       OR has_table_privilege('public.experiment_assignment', 'DELETE')
       OR has_table_privilege('public.experiment_assignment', 'TRUNCATE') THEN
        RAISE EXCEPTION 'experiment_assignment must be append-only for the application role (RN-42)';
    END IF;
    RAISE NOTICE 'PASS: experiment_assignment is append-only for the application role';

    IF NOT has_table_privilege('public.experiment_exposure', 'SELECT')
       OR NOT has_table_privilege('public.experiment_exposure', 'INSERT')
       OR has_table_privilege('public.experiment_exposure', 'UPDATE')
       OR has_table_privilege('public.experiment_exposure', 'DELETE')
       OR has_table_privilege('public.experiment_exposure', 'TRUNCATE') THEN
        RAISE EXCEPTION 'experiment_exposure must be append-only for the application role (RN-27)';
    END IF;
    RAISE NOTICE 'PASS: experiment_exposure is append-only for the application role';

    IF NOT has_table_privilege('public.experiment_conversion', 'SELECT')
       OR NOT has_table_privilege('public.experiment_conversion', 'INSERT')
       OR has_table_privilege('public.experiment_conversion', 'UPDATE')
       OR has_table_privilege('public.experiment_conversion', 'DELETE')
       OR has_table_privilege('public.experiment_conversion', 'TRUNCATE') THEN
        RAISE EXCEPTION 'experiment_conversion must be append-only for the application role (RN-27)';
    END IF;
    RAISE NOTICE 'PASS: experiment_conversion is append-only for the application role';

    IF NOT has_table_privilege('public.experiment', 'UPDATE')
       OR has_table_privilege('public.experiment', 'DELETE')
       OR has_table_privilege('public.experiment_group', 'DELETE') THEN
        RAISE EXCEPTION 'experiment and experiment_group must not be deletable by the application role (ADR-0029)';
    END IF;
    IF has_table_privilege('public.segmentation_run', 'UPDATE')
       OR has_table_privilege('public.segmentation_run', 'DELETE')
       OR NOT has_table_privilege('public.segmentation_run', 'INSERT') THEN
        RAISE EXCEPTION 'segmentation_run must be insert-only for the application role (ADR-0029)';
    END IF;
    IF has_table_privilege('public.customer_segment_history', 'UPDATE')
       OR has_table_privilege('public.customer_segment_history', 'DELETE')
       OR NOT has_column_privilege('public.customer_segment_history', 'valid_to', 'UPDATE')
       OR has_column_privilege('public.customer_segment_history', 'label_code', 'UPDATE')
       OR has_column_privilege('public.customer_segment_history', 'segment_id', 'UPDATE') THEN
        RAISE EXCEPTION 'customer_segment_history may only have valid_to updated by the application role (ADR-0029)';
    END IF;
    RAISE NOTICE 'PASS: history, runs and experiments are protected from the application role';

    BEGIN
        DROP TABLE public.inventory;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to DROP inventory';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: DROP TABLE inventory refused (SQLSTATE 42501)';
    END;

    SELECT COALESCE(max(audit_id), 0) INTO audit_before FROM public.audit_log;
    INSERT INTO public.category (category_id, name)
    VALUES (32767, 'Application role verification');
    UPDATE public.category SET name = 'Updated role verification' WHERE category_id = 32767;
    IF (SELECT name FROM public.category WHERE category_id = 32767)
       IS DISTINCT FROM 'Updated role verification' THEN
        RAISE EXCEPTION 'Application role could not read its write';
    END IF;
    DELETE FROM public.category WHERE category_id = 32767;
    IF EXISTS (SELECT FROM public.category WHERE category_id = 32767) THEN
        RAISE EXCEPTION 'Application role could not delete its row';
    END IF;
    IF (SELECT count(DISTINCT action) FROM public.audit_log
        WHERE audit_id > audit_before AND entity = 'category' AND entity_pk = '32767'
          AND action IN ('INSERT', 'UPDATE', 'DELETE')) <> 3 THEN
        RAISE EXCEPTION 'Audited writes did not record all three actions';
    END IF;
    BEGIN
        UPDATE public.audit_log SET entity = entity WHERE audit_id > audit_before;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to UPDATE audit_log';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: UPDATE audit_log refused (SQLSTATE 42501)';
    END;
    BEGIN
        DELETE FROM public.audit_log WHERE audit_id > audit_before;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to DELETE audit_log';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: DELETE audit_log refused (SQLSTATE 42501)';
    END;
    BEGIN
        UPDATE public.experiment_assignment SET group_id = group_id
         WHERE assignment_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to UPDATE experiment_assignment';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: UPDATE experiment_assignment refused (SQLSTATE 42501)';
    END;
    BEGIN
        DELETE FROM public.experiment_assignment WHERE assignment_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to DELETE experiment_assignment';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: DELETE experiment_assignment refused (SQLSTATE 42501)';
    END;
    BEGIN
        UPDATE public.experiment_exposure SET exposed_at = exposed_at
         WHERE exposure_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to UPDATE experiment_exposure';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: UPDATE experiment_exposure refused (SQLSTATE 42501)';
    END;
    BEGIN
        DELETE FROM public.experiment_exposure WHERE exposure_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to DELETE experiment_exposure';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: DELETE experiment_exposure refused (SQLSTATE 42501)';
    END;
    BEGIN
        UPDATE public.experiment_conversion SET converted_at = converted_at
         WHERE conversion_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to UPDATE experiment_conversion';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: UPDATE experiment_conversion refused (SQLSTATE 42501)';
    END;
    BEGIN
        DELETE FROM public.experiment_conversion WHERE conversion_id = -1;
        RAISE EXCEPTION 'FAIL: retail_app was allowed to DELETE experiment_conversion';
    EXCEPTION WHEN insufficient_privilege THEN
        RAISE NOTICE 'PASS: DELETE experiment_conversion refused (SQLSTATE 42501)';
    END;
    BEGIN
        INSERT INTO public.experiment_exposure (assignment_id)
        SELECT a.assignment_id
          FROM public.experiment_assignment AS a
          JOIN public.experiment_group AS g
            ON g.group_id = a.group_id
           AND g.experiment_id = a.experiment_id
         WHERE g.kind = 'CONTROL'
         ORDER BY a.assignment_id
         LIMIT 1;
        RAISE EXCEPTION 'FAIL: retail_app exposed a control-group assignment';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: control-group exposure refused (SQLSTATE 23514)';
    END;

    -- ADR-0046: service-owned data lives in service schemas the web never
    -- reaches.
    IF EXISTS (
        SELECT FROM pg_namespace
        WHERE nspname NOT IN ('public', 'information_schema')
          AND nspname NOT LIKE 'pg\_%'
          AND has_schema_privilege(oid, 'USAGE')
    ) THEN
        RAISE EXCEPTION 'Application role can reach a schema other than public';
    END IF;
    RAISE NOTICE 'PASS: no service schema is reachable by the application role';

    -- ADR-0039: `svc-sales:` belongs to sales-service. The web's insert is
    -- refused by the BOUNDARY sales guard with 22023, which ingest_row
    -- already reports as a malformed row; any other outcome fails.
    BEGIN
        INSERT INTO public.transaction
            (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total,
             data_origin)
        SELECT 'svc-sales:00000000-0000-0000-0000-000000000000',
               (SELECT customer_id FROM public.customer ORDER BY customer_id LIMIT 1),
               (SELECT store_id FROM public.store ORDER BY store_id LIMIT 1),
               (SELECT channel_id FROM public.channel ORDER BY channel_id LIMIT 1),
               now(), 0, 'OBSERVED';
        RAISE EXCEPTION 'FAIL: retail_app wrote into the svc-sales: namespace';
    EXCEPTION WHEN invalid_parameter_value THEN
        RAISE NOTICE 'PASS: svc-sales: namespace refused (SQLSTATE 22023)';
    END;

    -- ADR-0062: a line's returned_quantity is written by svc_sales alone. The
    -- web's update is refused by the BOUNDARY sales guard with 22023, and the
    -- views it reads for net units are read-only to it.
    BEGIN
        UPDATE public.transaction_line SET returned_quantity = 0
         WHERE (transaction_id, product_id) = (
               SELECT transaction_id, product_id FROM public.transaction_line
                ORDER BY transaction_id, product_id LIMIT 1);
        RAISE EXCEPTION 'FAIL: retail_app wrote returned_quantity';
    EXCEPTION WHEN invalid_parameter_value THEN
        RAISE NOTICE 'PASS: returned_quantity refused (SQLSTATE 22023)';
    END;
    IF NOT has_table_privilege('public.net_sale', 'SELECT')
       OR NOT has_table_privilege('public.net_sale_line', 'SELECT')
       OR has_table_privilege('public.net_sale', 'INSERT, UPDATE, DELETE')
       OR has_table_privilege('public.net_sale_line', 'INSERT, UPDATE, DELETE') THEN
        RAISE EXCEPTION 'FAIL: the net sale views are not read-only to the application role';
    END IF;
    RAISE NOTICE 'PASS: net sale views are read-only';
    RAISE NOTICE 'PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access';
END;
$verify$;
ROLLBACK;
\endif
