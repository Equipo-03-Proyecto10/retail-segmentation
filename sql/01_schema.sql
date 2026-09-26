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
    description VARCHAR(160)
);

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
    email         VARCHAR(160) NOT NULL UNIQUE,
    password_hash VARCHAR(255) NOT NULL,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (email ~ '@')
);

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

CREATE TABLE customer (
    customer_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID UNIQUE REFERENCES app_user(user_id) ON DELETE SET NULL,
    name                    VARCHAR(120) NOT NULL,
    email                   VARCHAR(160) UNIQUE,
    phone                   VARCHAR(20),
    registration_channel_id SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    registered_on           DATE NOT NULL DEFAULT CURRENT_DATE,
    CHECK (email IS NULL OR email ~ '@')
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
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

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
    UNIQUE (run_id, customer_id)
);

-- Exactly one open row per customer — the partial unique index a concurrent
-- writer collides against, not just an application-level check.
CREATE UNIQUE INDEX ux_customer_segment_history_open
    ON customer_segment_history (customer_id) WHERE valid_to IS NULL;

CREATE INDEX idx_customer_segment_history_customer
    ON customer_segment_history (customer_id, valid_from);

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
    -- multi-producer contract would need to change here.
    source_transaction_id  VARCHAR(64) NOT NULL UNIQUE,
    customer_id            UUID NOT NULL REFERENCES customer(customer_id) ON DELETE RESTRICT,
    store_id               SMALLINT NOT NULL REFERENCES store(store_id) ON DELETE RESTRICT,
    channel_id             SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    occurred_at            TIMESTAMPTZ NOT NULL,
    total                  NUMERIC(12,2) NOT NULL CHECK (total >= 0)
);

CREATE TABLE transaction_line (
    transaction_id BIGINT NOT NULL REFERENCES transaction(transaction_id) ON DELETE CASCADE,
    product_id     INT NOT NULL REFERENCES product(product_id) ON DELETE RESTRICT,
    quantity       INT NOT NULL CHECK (quantity > 0),
    unit_price     NUMERIC(10,2) NOT NULL CHECK (unit_price >= 0),
    PRIMARY KEY (transaction_id, product_id)
);

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

CREATE TABLE experiment_group (
    group_id      INT PRIMARY KEY,
    experiment_id INT NOT NULL REFERENCES experiment(experiment_id) ON DELETE CASCADE,
    kind          VARCHAR(20) NOT NULL CHECK (kind IN ('CONTROL','TREATMENT')),
    -- Lets experiment_assignment's FK pin a row to both its group and that
    -- group's experiment at once, so an assignment can never claim a group
    -- belonging to a different experiment.
    UNIQUE (group_id, experiment_id)
);

-- At most one control group per experiment (ADR-0019), the same shape as
-- ux_app_user_single_administrator. At least one treatment group before
-- activation is not expressible here -- nothing forces a row to exist -- and
-- is F11-03's service-level check.
CREATE UNIQUE INDEX ux_experiment_one_control
    ON experiment_group (experiment_id) WHERE kind = 'CONTROL';

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

-- A later, separate event: an assigned customer may remain unexposed
-- (ADR-0019), so exposure is never folded into the assignment row. More
-- than one exposure per assignment is allowed -- "each later exposure" is
-- plural in the ADR -- so there is no uniqueness constraint here.
CREATE TABLE experiment_exposure (
    exposure_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id BIGINT NOT NULL REFERENCES experiment_assignment(assignment_id) ON DELETE CASCADE,
    exposed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Conversion links an assignment to a qualifying sale rather than adding an
-- experiment column to transaction (ADR-0019). UNIQUE stops the same sale
-- being recorded as a conversion twice for the same assignment; it does not
-- limit an assignment to one conversion, since an average-ticket metric can
-- legitimately draw on more than one qualifying sale.
CREATE TABLE experiment_conversion (
    conversion_id  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    assignment_id  BIGINT NOT NULL REFERENCES experiment_assignment(assignment_id) ON DELETE CASCADE,
    transaction_id BIGINT NOT NULL REFERENCES transaction(transaction_id) ON DELETE CASCADE,
    converted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (assignment_id, transaction_id)
);

-- ---------- INVENTORY ----------

CREATE TABLE inventory (
    store_id          SMALLINT NOT NULL REFERENCES store(store_id) ON DELETE CASCADE,
    product_id        INT NOT NULL REFERENCES product(product_id) ON DELETE CASCADE,
    quantity_on_hand  INT NOT NULL CHECK (quantity_on_hand >= 0),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (store_id, product_id)
);

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
$$ LANGUAGE plpgsql;

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

-- ADR-0017 retires customer.current_segment_id in favour of durable history;
-- this trigger is what replaces the audit signal that used to come from
-- updating that column. Every open/close pair a segmentation run writes
-- (web/db/segments.py) is now traceable the same way a catalog edit is.
CREATE TRIGGER trg_audit_customer_segment_history
AFTER INSERT OR UPDATE OR DELETE ON customer_segment_history
FOR EACH ROW EXECUTE FUNCTION fn_audit('history_id');

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
          AND (NOT has_table_privilege(c.oid, 'SELECT')
               OR NOT has_table_privilege(c.oid, 'INSERT')
               OR NOT has_table_privilege(c.oid, 'UPDATE')
               OR NOT has_table_privilege(c.oid, 'DELETE')
               OR has_table_privilege(c.oid, 'TRUNCATE'))
    ) THEN
        RAISE EXCEPTION 'Application table privileges differ from the DML-only policy';
    END IF;
    RAISE NOTICE 'PASS: restricted role, no ownership or CREATE, DML on all tables';

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
    RAISE NOTICE 'PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access';
END;
$verify$;
ROLLBACK;
\endif
