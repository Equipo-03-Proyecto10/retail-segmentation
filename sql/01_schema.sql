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
    parent_category_id SMALLINT REFERENCES category(category_id) ON DELETE RESTRICT
);

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

CREATE TABLE segment (
    segment_id  INT PRIMARY KEY,
    name        VARCHAR(80) NOT NULL UNIQUE,
    description VARCHAR(255),
    rule_id     INT NOT NULL REFERENCES segment_rule(rule_id) ON DELETE RESTRICT,
    valid_from  DATE NOT NULL,
    valid_to    DATE,
    CHECK (valid_to IS NULL OR valid_to >= valid_from)
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

-- The partial unique index that enforces the single-administrator rule in the
-- schema is F4-02 (#70), which adds it together with the application-side
-- check. AGENTS.md is explicit that one half alone does not count: the
-- application check alone is bypassed by a direct INSERT, and the index alone
-- surfaces as an unexplained database error. 02_seed_30_per_table.sql already
-- seeds exactly one administrator, so the index applies cleanly when it lands.

CREATE TABLE customer (
    customer_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id                 UUID UNIQUE REFERENCES app_user(user_id) ON DELETE SET NULL,
    name                    VARCHAR(120) NOT NULL,
    email                   VARCHAR(160) UNIQUE,
    phone                   VARCHAR(20),
    registration_channel_id SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    current_segment_id      INT REFERENCES segment(segment_id) ON DELETE SET NULL,
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
    transaction_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id    UUID NOT NULL REFERENCES customer(customer_id) ON DELETE RESTRICT,
    store_id       SMALLINT NOT NULL REFERENCES store(store_id) ON DELETE RESTRICT,
    channel_id     SMALLINT NOT NULL REFERENCES channel(channel_id) ON DELETE RESTRICT,
    occurred_at    TIMESTAMPTZ NOT NULL,
    total          NUMERIC(12,2) NOT NULL CHECK (total >= 0)
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
    segment_id  INT NOT NULL REFERENCES segment(segment_id) ON DELETE RESTRICT,
    starts_on   DATE NOT NULL,
    ends_on     DATE NOT NULL,
    status      VARCHAR(20) NOT NULL CHECK (status IN ('DRAFT','ACTIVE','FINISHED','CANCELLED')),
    CHECK (ends_on >= starts_on)
);

CREATE TABLE experiment (
    experiment_id INT PRIMARY KEY,
    name          VARCHAR(120) NOT NULL,
    campaign_id   INT REFERENCES campaign(campaign_id) ON DELETE SET NULL,
    target_metric VARCHAR(60) NOT NULL,
    starts_on     DATE NOT NULL,
    ends_on       DATE,
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);

CREATE TABLE experiment_group (
    group_id      INT PRIMARY KEY,
    experiment_id INT NOT NULL REFERENCES experiment(experiment_id) ON DELETE CASCADE,
    kind          VARCHAR(20) NOT NULL CHECK (kind IN ('CONTROL','TREATMENT'))
);

CREATE TABLE experiment_group_customer (
    group_id    INT NOT NULL REFERENCES experiment_group(group_id) ON DELETE CASCADE,
    customer_id UUID NOT NULL REFERENCES customer(customer_id) ON DELETE CASCADE,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (group_id, customer_id)
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
CREATE INDEX idx_customer_segment                 ON customer (current_segment_id);
CREATE INDEX idx_product_category                 ON product (category_id);
CREATE INDEX idx_campaign_segment                 ON campaign (segment_id);
CREATE INDEX idx_experiment_group_customer_cust   ON experiment_group_customer (customer_id);
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
