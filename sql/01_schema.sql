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
    email                   VARCHAR(160) UNIQUE,
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
    run_at         TIMESTAMPTZ NOT NULL DEFAULT now()
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
    -- BOUNDARY sales refuse it from any other session user, and refuse
    -- anything else from svc_sales.
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
-- The bench-only Rust build of the products reads: SELECT on product, nothing
-- else (ADR-0042).
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
-- sales-service's own story (F14-18) extends this section and does not
-- re-create these guards.

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
-- END BOUNDARY sales

-- BEGIN BOUNDARY products
-- products-service (F14-06, ADR-0034, ADR-0044). Its contract view, its
-- outbox for stream:products.v1.changed and the grants of svc_products. The
-- capture trigger fires for every writer of public.product, the frozen web
-- and a hard delete included, so no change is invisible to the cache
-- invalidation of products and of recommendations.

CREATE SCHEMA IF NOT EXISTS api_products;
REVOKE ALL ON SCHEMA api_products FROM PUBLIC;
CREATE SCHEMA IF NOT EXISTS products_events;
REVOKE ALL ON SCHEMA products_events FROM PUBLIC;

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

-- contracts/db/api_products.product_ref.v1.md. Definer rights
-- (security_invoker = off): a consumer reads these columns and nothing else of
-- public.product. image_path stays web-managed and is not published.
CREATE OR REPLACE VIEW api_products.product_ref_v1
WITH (security_invoker = off) AS
SELECT product_id, sku, name, category_id, list_price, is_active
FROM public.product;

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
-- END BOUNDARY products

-- BEGIN BOUNDARY customers
-- customers-service (F14-14) owns the registration rule of RN-08: email unique
-- when given (customer_email_key), a known channel
-- (customer_registration_channel_id_fkey), a registration date not in the
-- future (checked by the service) and no deletion. The table stays in public
-- as legacy, and the web only reads it. The boundary publishes no stream, so it
-- has no outbox and no capture trigger.

CREATE SCHEMA IF NOT EXISTS api_customers;
REVOKE ALL ON SCHEMA api_customers FROM PUBLIC;

-- The published customer reference: identity and registration date, and no
-- personal data. security_invoker = off, so a consumer needs no privilege on
-- public.customer.
CREATE OR REPLACE VIEW api_customers.customer_ref_v1
WITH (security_invoker = off) AS
SELECT customer_id, registered_on
FROM public.customer;
REVOKE ALL ON api_customers.customer_ref_v1 FROM PUBLIC;

-- The only service writer of customer: no DELETE, and registered_on and
-- user_id can be set at registration but never corrected.
GRANT SELECT, INSERT ON public.customer TO svc_customers;
GRANT UPDATE (name, email, phone, registration_channel_id)
    ON public.customer TO svc_customers;

GRANT USAGE ON SCHEMA api_customers
    TO svc_customers, svc_sales, svc_preferences, svc_segments,
       svc_recommendations, svc_engagement;
GRANT SELECT ON api_customers.customer_ref_v1
    TO svc_customers, svc_sales, svc_preferences, svc_segments,
       svc_recommendations, svc_engagement;
-- END BOUNDARY customers

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

GRANT USAGE ON SCHEMA stores TO svc_stores;
GRANT SELECT, INSERT, UPDATE, DELETE ON stores.store_location TO svc_stores;
GRANT SELECT ON public.store TO svc_stores;
GRANT USAGE ON SCHEMA stores_events TO svc_stores, definer_stores;
GRANT INSERT ON stores_events.outbox TO definer_stores;
GRANT SELECT, DELETE ON stores_events.outbox TO svc_stores;
GRANT UPDATE (relayed_at) ON stores_events.outbox TO svc_stores;
GRANT USAGE ON SCHEMA api_stores TO svc_stores, svc_checkins, svc_engagement;
GRANT SELECT ON api_stores.store_directory_v1 TO svc_stores, svc_checkins, svc_engagement;
-- END BOUNDARY stores

-- BEGIN BOUNDARY users
-- users-service (F14-04, ADR-0034, ADR-0036, ADR-0044): the single writer of
-- accounts besides the frozen web (E-09). It publishes the credential view
-- auth reads (E-15) and captures every access change, the web's included, so
-- auth can end the user's sessions. Idempotent: an operator re-applies it.

CREATE SCHEMA IF NOT EXISTS api_users;
CREATE SCHEMA IF NOT EXISTS users_events;
REVOKE ALL ON SCHEMA api_users FROM PUBLIC;
REVOKE ALL ON SCHEMA users_events FROM PUBLIC;

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

GRANT USAGE ON SCHEMA users_events TO svc_users, definer_users;
GRANT INSERT ON users_events.outbox TO definer_users;
GRANT SELECT, DELETE, UPDATE (relayed_at) ON users_events.outbox TO svc_users;

GRANT USAGE ON SCHEMA api_users TO svc_users, svc_auth;
GRANT SELECT ON api_users.credential_v1 TO svc_users, svc_auth;
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
            (source_transaction_id, customer_id, store_id, channel_id, occurred_at, total)
        SELECT 'svc-sales:00000000-0000-0000-0000-000000000000',
               (SELECT customer_id FROM public.customer ORDER BY customer_id LIMIT 1),
               (SELECT store_id FROM public.store ORDER BY store_id LIMIT 1),
               (SELECT channel_id FROM public.channel ORDER BY channel_id LIMIT 1),
               now(), 0;
        RAISE EXCEPTION 'FAIL: retail_app wrote into the svc-sales: namespace';
    EXCEPTION WHEN invalid_parameter_value THEN
        RAISE NOTICE 'PASS: svc-sales: namespace refused (SQLSTATE 22023)';
    END;
    RAISE NOTICE 'PASS: SELECT, INSERT, UPDATE, DELETE and audit sequence access';
END;
$verify$;
ROLLBACK;
\endif
