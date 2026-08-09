"""Frozen M1 schema: 27 tables, 3 views, 75 indexes.

Reproduces ``infra/sql/schema/001_m1_initial_schema.sql``. That file is the
authoritative physical model (S0-04), and its own header states the contract
this revision has to satisfy:

    The first substantive Alembic revision must produce a schema byte-identical
    to this file.

The SQL is embedded verbatim rather than read from disk at run time. An applied
revision must be immutable: a migration that reads a file which can later change
is a migration whose history is not reproducible. The single edit is removal of
the file's outer ``BEGIN;`` / ``COMMIT;``, because Alembic already runs each
revision inside a transaction and a nested one would abort the run.

SHA-256 of the source file when this revision was written:
388872ddb856e259de60692736010939eda1a3e1207e749a48ec165e3cec69cb

The checksum records provenance. It does not prove equivalence: that is proven
by diffing a database built by this revision against one built by the reference
DDL, which is the verification the DDL header prescribes. See ``web/README.md``.

Revision ID: 0001_frozen_m1_schema
Revises: none, this is the baseline
Create Date: 2026-08-09
"""

from alembic import op

revision = "0001_frozen_m1_schema"
down_revision = None
branch_labels = None
depends_on = None


# Verbatim copy of infra/sql/schema/001_m1_initial_schema.sql.
# Outer BEGIN;/COMMIT; removed; nothing else altered.
SCHEMA_SQL = r"""
-- =============================================================================
-- Milestone 1 — Frozen physical schema
-- Project : Dynamic Segmentation and Retail Personalization Platform (Team 03)
-- Story   : S0-04
-- Target  : PostgreSQL 16
-- Status  : PROPOSED — freeze at Sprint 0 Planning, 2026-08-10
-- Companion: docs/data/postgresql-model.md (decisions, rationale, queries)
--
-- This file is the authoritative physical model. The first substantive Alembic
-- revision must produce a schema byte-identical to this file. Verification:
--   psql -f 001_m1_initial_schema.sql -d ref_db
--   alembic upgrade head            -d app_db
--   migra ref_db app_db             -> must output nothing
--
-- Conventions (see docs/data/postgresql-model.md §2):
--   singular snake_case table names; surrogate PK named id
--   timestamptz everywhere, stored UTC; date for calendar-only values
--   numeric(14,2) for money; never float
--   no PostgreSQL ENUM types; varchar + CHECK, or a lookup table
--   soft delete only on catalog tables, via deleted_at
--   index prefixes: pk_ ux_ ix_ ck_ fk_ ex_
-- =============================================================================


CREATE EXTENSION IF NOT EXISTS btree_gist;  -- required by the EXCLUDE constraints
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- catalog substring search

-- -----------------------------------------------------------------------------
-- 0. Shared triggers
-- -----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION fn_set_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := now();
    RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION fn_reject_write() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'table %.% is append-only; % rejected',
        TG_TABLE_SCHEMA, TG_TABLE_NAME, TG_OP;
END;
$$;

-- =============================================================================
-- 1. LOOKUPS
-- Small, stable, natural-keyed. Referenced by code so that JSON/XML payloads
-- and the mobile/desktop clients never depend on surrogate integers.
-- =============================================================================

CREATE TABLE sales_channel (
    code            varchar(30)  PRIMARY KEY,
    name            varchar(80)  NOT NULL,
    requires_store  boolean      NOT NULL,
    is_digital      boolean      NOT NULL,
    display_order   smallint     NOT NULL DEFAULT 0
);
COMMENT ON TABLE sales_channel IS
'Purchase channel. First-class because "channel switch" is a required migration dimension, distinct from "store switch".';

INSERT INTO sales_channel (code, name, requires_store, is_digital, display_order) VALUES
    ('in_store',    'In store',    true,  false, 1),
    ('web',         'Web',         false, true,  2),
    ('mobile_app',  'Mobile app',  false, true,  3),
    ('marketplace', 'Marketplace', false, true,  4),
    ('call_center', 'Call center', false, false, 5);

CREATE TABLE segment_label (
    code          varchar(40) PRIMARY KEY,
    name          varchar(80) NOT NULL,
    description   text        NOT NULL,
    value_rank    smallint    NOT NULL,
    color_hex     char(7),
    display_order smallint    NOT NULL DEFAULT 0,
    is_active     boolean     NOT NULL DEFAULT true,
    CONSTRAINT ux_segment_label_value_rank UNIQUE (value_rank)
);
COMMENT ON TABLE segment_label IS
'Stable segment identity across runs. Clusters are run-scoped and their indices are not stable between runs; every cross-run comparison (migration, campaign targeting, uplift) joins on this code, never on segment.id.';
COMMENT ON COLUMN segment_label.value_rank IS
'Monotonic business value ordering. Enables migration direction (upgrade / lateral / downgrade) without hard-coded logic.';

INSERT INTO segment_label (code, name, description, value_rank, display_order) VALUES
    ('lost',        'Lost',        'No purchase in the long tail of the window; lowest value.',        1, 1),
    ('hibernating', 'Hibernating', 'Long recency, low frequency, historically low spend.',             2, 2),
    ('at_risk',     'At risk',     'Previously valuable, recency deteriorating.',                      3, 3),
    ('promising',   'Promising',   'Recent but still low frequency and spend.',                        4, 4),
    ('loyal',       'Loyal',       'High frequency, moderate spend, healthy recency.',                 5, 5),
    ('champions',   'Champions',   'Recent, frequent, highest spend.',                                 6, 6);

CREATE TABLE consent_purpose (
    code                    varchar(40)  PRIMARY KEY,
    name                    varchar(120) NOT NULL,
    description             text         NOT NULL,
    is_required_for_service boolean      NOT NULL DEFAULT false,
    legal_basis             varchar(60)  NOT NULL
);
COMMENT ON TABLE consent_purpose IS
'Purpose limitation under LFPDPPP. Each purpose is consented separately; profiling is never bundled with service delivery.';

INSERT INTO consent_purpose (code, name, description, is_required_for_service, legal_basis) VALUES
    ('loyalty_membership', 'Loyalty membership',
     'Operate the loyalty account, purchase history and point balance.', true,  'contract'),
    ('behavioral_profiling', 'Behavioral profiling',
     'Compute RFM, assign consumer segments and detect segment migration.', false, 'consent'),
    ('personalized_offers', 'Personalized offers',
     'Generate and deliver product recommendations and targeted promotions.', false, 'consent'),
    ('marketing_contact', 'Marketing contact',
     'Send commercial messages through email, push notification or SMS.', false, 'consent'),
    ('experimentation', 'Experimentation',
     'Include the customer in A/B experiments and control groups.', false, 'consent');

CREATE TABLE privacy_notice_version (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    version         varchar(20)  NOT NULL,
    published_at    timestamptz  NOT NULL,
    document_uri    text         NOT NULL,
    checksum_sha256 char(64)     NOT NULL,
    is_current      boolean      NOT NULL DEFAULT false,
    CONSTRAINT ux_privacy_notice_version_version UNIQUE (version)
);
CREATE UNIQUE INDEX ux_privacy_notice_version_current
    ON privacy_notice_version (is_current) WHERE is_current;

-- =============================================================================
-- 2. MASTER DATA
-- =============================================================================

CREATE TABLE store (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code          varchar(30)  NOT NULL,
    name          varchar(160) NOT NULL,
    region        varchar(80)  NOT NULL,
    city          varchar(120),
    country       char(2)      NOT NULL DEFAULT 'MX',
    latitude      numeric(9,6),
    longitude     numeric(9,6),
    store_format  varchar(30)  NOT NULL DEFAULT 'standard',
    opened_on     date,
    is_active     boolean      NOT NULL DEFAULT true,
    created_at    timestamptz  NOT NULL DEFAULT now(),
    updated_at    timestamptz  NOT NULL DEFAULT now(),
    deleted_at    timestamptz,
    CONSTRAINT ck_store_latitude  CHECK (latitude  IS NULL OR latitude  BETWEEN  -90 AND  90),
    CONSTRAINT ck_store_longitude CHECK (longitude IS NULL OR longitude BETWEEN -180 AND 180)
);
CREATE UNIQUE INDEX ux_store_code ON store (code) WHERE deleted_at IS NULL;
CREATE TRIGGER trg_store_updated_at BEFORE UPDATE ON store
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TABLE category (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    parent_category_id bigint       REFERENCES category(id),
    code               varchar(40)  NOT NULL,
    name               varchar(120) NOT NULL,
    depth              smallint     NOT NULL DEFAULT 1,
    is_active          boolean      NOT NULL DEFAULT true,
    created_at         timestamptz  NOT NULL DEFAULT now(),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    deleted_at         timestamptz,
    CONSTRAINT ck_category_not_own_parent CHECK (parent_category_id IS DISTINCT FROM id),
    CONSTRAINT ck_category_depth CHECK (depth BETWEEN 1 AND 4)
);
CREATE UNIQUE INDEX ux_category_code ON category (code) WHERE deleted_at IS NULL;
CREATE INDEX ix_category_parent ON category (parent_category_id);
CREATE TRIGGER trg_category_updated_at BEFORE UPDATE ON category
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TABLE customer (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system        varchar(40)  NOT NULL DEFAULT 'seed',
    external_customer_id varchar(64),
    email                varchar(255),
    full_name            varchar(160),
    phone                varchar(30),
    birth_date           date,
    country              char(2),
    region               varchar(80),
    preferred_store_id   bigint       REFERENCES store(id),
    enrolled_at          timestamptz,
    loyalty_tier         varchar(20),
    status               varchar(20)  NOT NULL DEFAULT 'active',
    created_at           timestamptz  NOT NULL DEFAULT now(),
    updated_at           timestamptz  NOT NULL DEFAULT now(),
    deleted_at           timestamptz,
    CONSTRAINT ck_customer_status CHECK (status IN ('active','inactive','anonymized')),
    CONSTRAINT ux_customer_source_external UNIQUE (source_system, external_customer_id)
);
COMMENT ON COLUMN customer.preferred_store_id IS
'Declared by the customer at enrollment. The store they actually shop at is derived per analysis window in customer_rfm_snapshot.dominant_store_id — never overwrite one with the other.';
CREATE UNIQUE INDEX ux_customer_email
    ON customer (lower(email)) WHERE deleted_at IS NULL AND email IS NOT NULL;
CREATE INDEX ix_customer_preferred_store ON customer (preferred_store_id);
CREATE TRIGGER trg_customer_updated_at BEFORE UPDATE ON customer
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

-- =============================================================================
-- 3. IDENTITY AND ACCESS
-- =============================================================================

CREATE TABLE user_account (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    email               varchar(255) NOT NULL,
    password_hash       varchar(255) NOT NULL,
    password_algorithm  varchar(20)  NOT NULL DEFAULT 'argon2id',
    full_name           varchar(160) NOT NULL,
    customer_id         bigint       REFERENCES customer(id),
    status              varchar(20)  NOT NULL DEFAULT 'active',
    permission_version  integer      NOT NULL DEFAULT 1,
    failed_login_count  smallint     NOT NULL DEFAULT 0,
    locked_until        timestamptz,
    last_login_at       timestamptz,
    password_changed_at timestamptz  NOT NULL DEFAULT now(),
    must_change_password boolean     NOT NULL DEFAULT false,
    created_at          timestamptz  NOT NULL DEFAULT now(),
    updated_at          timestamptz  NOT NULL DEFAULT now(),
    deleted_at          timestamptz,
    CONSTRAINT ck_user_account_status CHECK (status IN ('active','suspended','deactivated')),
    CONSTRAINT ux_user_account_customer UNIQUE (customer_id)
);
COMMENT ON COLUMN user_account.customer_id IS
'Non-null only for consumer logins (mobile app, loyalty portal). Staff accounts have no customer row; most customers have no account. See ADR-001.';
COMMENT ON COLUMN user_account.permission_version IS
'Incremented whenever the effective permission set changes. Carried as a JWT claim so a permission change invalidates outstanding access tokens without a database read on every request.';
CREATE UNIQUE INDEX ux_user_account_email
    ON user_account (lower(email)) WHERE deleted_at IS NULL;
CREATE TRIGGER trg_user_account_updated_at BEFORE UPDATE ON user_account
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TABLE role (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        varchar(50)  NOT NULL,
    name        varchar(120) NOT NULL,
    description text         NOT NULL,
    is_system   boolean      NOT NULL DEFAULT false,
    scope_kind  varchar(20)  NOT NULL DEFAULT 'global',
    created_at  timestamptz  NOT NULL DEFAULT now(),
    updated_at  timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ux_role_code UNIQUE (code),
    CONSTRAINT ck_role_scope_kind CHECK (scope_kind IN ('global','store'))
);
COMMENT ON COLUMN role.scope_kind IS
'Whether assignments of this role must name a store. store_manager is store-scoped; the other six profiles are global.';

INSERT INTO role (code, name, description, is_system, scope_kind) VALUES
    ('administrator',     'Administrator',      'Full administrative access to users, roles, catalogs and configuration.', true, 'global'),
    ('commercial_analyst','Commercial Analyst', 'Executes segmentation runs, reviews migrations and dashboards.',          true, 'global'),
    ('store_manager',     'Store Manager',      'Reads segments and active promotions for the assigned store only.',      true, 'store'),
    ('marketing',         'Marketing',          'Designs campaigns and experiments over existing segments.',               true, 'global'),
    ('inventory_planner', 'Inventory Planner',  'Reviews availability and segment-linked demand.',                         true, 'global'),
    ('auditor',           'Auditor',            'Read-only access to the audit trail and point-in-time segment history.',  true, 'global'),
    ('customer',          'Customer',           'Consumer profile for the public site and mobile application.',            true, 'global');

CREATE TABLE permission (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    code        varchar(100) NOT NULL,
    resource    varchar(60)  NOT NULL,
    action      varchar(30)  NOT NULL,
    description text         NOT NULL,
    CONSTRAINT ux_permission_code UNIQUE (code),
    CONSTRAINT ck_permission_action
        CHECK (action IN ('create','read','update','delete','execute','export','approve'))
);
COMMENT ON COLUMN permission.code IS
'Dotted resource.action, e.g. catalog.product.create, analytics.segmentation.execute. The web menu and the microservice authorizers both read this code; never hard-code role names at a call site.';

CREATE TABLE role_permission (
    role_id       bigint      NOT NULL REFERENCES role(id) ON DELETE CASCADE,
    permission_id bigint      NOT NULL REFERENCES permission(id) ON DELETE CASCADE,
    granted_at    timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT pk_role_permission PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE user_role (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_account_id     bigint      NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    role_id             bigint      NOT NULL REFERENCES role(id),
    scope_store_id      bigint      REFERENCES store(id),
    assigned_by_user_id bigint      REFERENCES user_account(id),
    valid_from          timestamptz NOT NULL DEFAULT now(),
    valid_to            timestamptz,
    CONSTRAINT ck_user_role_validity CHECK (valid_to IS NULL OR valid_to > valid_from)
);
COMMENT ON COLUMN user_role.scope_store_id IS
'Row-level authorization scope. Present for store-scoped roles, null for global roles. Consistency with role.scope_kind is enforced by trigger because a CHECK cannot read the parent row.';
CREATE UNIQUE INDEX ux_user_role_open
    ON user_role (user_account_id, role_id, scope_store_id)
    NULLS NOT DISTINCT
    WHERE valid_to IS NULL;
CREATE INDEX ix_user_role_user ON user_role (user_account_id) WHERE valid_to IS NULL;

CREATE OR REPLACE FUNCTION fn_user_role_scope_matches() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_scope_kind varchar(20);
BEGIN
    SELECT scope_kind INTO v_scope_kind FROM role WHERE id = NEW.role_id;
    IF v_scope_kind = 'store' AND NEW.scope_store_id IS NULL THEN
        RAISE EXCEPTION 'role_id % is store-scoped and requires scope_store_id', NEW.role_id;
    END IF;
    IF v_scope_kind = 'global' AND NEW.scope_store_id IS NOT NULL THEN
        RAISE EXCEPTION 'role_id % is global and must not carry scope_store_id', NEW.role_id;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_user_role_scope BEFORE INSERT OR UPDATE ON user_role
    FOR EACH ROW EXECUTE FUNCTION fn_user_role_scope_matches();

CREATE TABLE password_reset_token (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_account_id bigint       NOT NULL REFERENCES user_account(id) ON DELETE CASCADE,
    token_hash      char(64)     NOT NULL,
    requested_ip    inet,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    expires_at      timestamptz  NOT NULL,
    consumed_at     timestamptz,
    CONSTRAINT ux_password_reset_token_hash UNIQUE (token_hash),
    CONSTRAINT ck_password_reset_token_expiry CHECK (expires_at > created_at)
);
COMMENT ON COLUMN password_reset_token.token_hash IS
'SHA-256 of the token. The token itself is never persisted; a database dump must not permit account takeover.';
CREATE UNIQUE INDEX ux_password_reset_token_open
    ON password_reset_token (user_account_id) WHERE consumed_at IS NULL;
COMMENT ON INDEX ux_password_reset_token_open IS
'At most one outstanding token per account. Requesting a new reset must first consume the previous one, so an old link mailed to a compromised inbox stops working.';

-- =============================================================================
-- 4. CONSENT
-- Valid-time versioned, same convention as segment assignment.
-- =============================================================================

CREATE TABLE file_object (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    storage_backend     varchar(20)  NOT NULL DEFAULT 'gcs',
    bucket              varchar(120) NOT NULL,
    object_key          text         NOT NULL,
    original_filename   varchar(255) NOT NULL,
    content_type        varchar(120) NOT NULL,
    size_bytes          bigint       NOT NULL,
    checksum_sha256     char(64)     NOT NULL,
    purpose             varchar(40)  NOT NULL,
    uploaded_by_user_id bigint       REFERENCES user_account(id),
    uploaded_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ux_file_object_key UNIQUE (bucket, object_key),
    CONSTRAINT ck_file_object_backend CHECK (storage_backend IN ('gcs','local')),
    CONSTRAINT ck_file_object_size CHECK (size_bytes > 0),
    CONSTRAINT ck_file_object_purpose CHECK (purpose IN
        ('transaction_import','product_image','consent_evidence','privacy_notice','export','report'))
);

CREATE TABLE product (
    id              bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku             varchar(60)  NOT NULL,
    name            varchar(200) NOT NULL,
    description     text,
    category_id     bigint       NOT NULL REFERENCES category(id),
    brand           varchar(120),
    unit_price      numeric(14,2) NOT NULL,
    currency_code   char(3)      NOT NULL DEFAULT 'MXN',
    unit_of_measure varchar(20)  NOT NULL DEFAULT 'unit',
    attributes      jsonb        NOT NULL DEFAULT '{}'::jsonb,
    image_file_id   bigint       REFERENCES file_object(id),
    is_active       boolean      NOT NULL DEFAULT true,
    created_at      timestamptz  NOT NULL DEFAULT now(),
    updated_at      timestamptz  NOT NULL DEFAULT now(),
    deleted_at      timestamptz,
    CONSTRAINT ck_product_unit_price CHECK (unit_price >= 0)
);
COMMENT ON COLUMN product.attributes IS
'Typed attribute bag (material, size, colour, seasonality, dietary flags). Content-based recommendation (E-22) has no feature space without it, and adding it after 100k lines exist is a backfill exercise. Schema documented in docs/data/product-attribute-schema.md.';
CREATE UNIQUE INDEX ux_product_sku ON product (sku) WHERE deleted_at IS NULL;
CREATE INDEX ix_product_category ON product (category_id);
CREATE INDEX ix_product_attributes ON product USING gin (attributes);
CREATE INDEX ix_product_name_trgm ON product USING gin (name gin_trgm_ops);
CREATE TRIGGER trg_product_updated_at BEFORE UPDATE ON product
    FOR EACH ROW EXECUTE FUNCTION fn_set_updated_at();

CREATE TABLE consent_record (
    id                bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id       bigint      NOT NULL REFERENCES customer(id),
    purpose_code      varchar(40) NOT NULL REFERENCES consent_purpose(code),
    notice_version_id bigint      NOT NULL REFERENCES privacy_notice_version(id),
    decision          varchar(10) NOT NULL,
    source_channel    varchar(30) NOT NULL,
    captured_ip       inet,
    evidence_file_id  bigint      REFERENCES file_object(id),
    valid_from        timestamptz NOT NULL DEFAULT now(),
    valid_to          timestamptz,
    recorded_at       timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_consent_record_decision CHECK (decision IN ('granted','denied')),
    CONSTRAINT ck_consent_record_validity CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_consent_record_source CHECK (source_channel IN
        ('web_public','web_private','mobile','back_office','import')),
    CONSTRAINT ex_consent_record_no_overlap EXCLUDE USING gist (
        customer_id  WITH =,
        purpose_code WITH =,
        tstzrange(valid_from, valid_to) WITH &&
    )
);
COMMENT ON TABLE consent_record IS
'One row per customer per purpose per validity interval. Withdrawal closes the interval and opens a denial; nothing is ever updated in place, so the state of consent at the moment of any past segmentation run is reconstructible.';

-- =============================================================================
-- 5. TRANSACTIONS AND INGESTION
-- =============================================================================

CREATE TABLE ingestion_run (
    id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind                varchar(30) NOT NULL,
    file_object_id      bigint      REFERENCES file_object(id),
    status              varchar(20) NOT NULL DEFAULT 'running',
    triggered_by_user_id bigint     REFERENCES user_account(id),
    correlation_id      uuid        NOT NULL,
    started_at          timestamptz NOT NULL DEFAULT now(),
    completed_at        timestamptz,
    rows_read           integer     NOT NULL DEFAULT 0,
    rows_accepted       integer     NOT NULL DEFAULT 0,
    rows_rejected       integer     NOT NULL DEFAULT 0,
    rows_duplicate      integer     NOT NULL DEFAULT 0,
    telemetry_ref       varchar(80),
    CONSTRAINT ck_ingestion_run_kind CHECK (kind IN ('transaction_csv','seed_dataset','catalog_csv')),
    CONSTRAINT ck_ingestion_run_status CHECK (status IN ('running','succeeded','partial','failed'))
);
COMMENT ON COLUMN ingestion_run.telemetry_ref IS
'MongoDB ObjectId of the matching ingestion telemetry document. Deliberately a plain string: no referential integrity is claimed across stores. PostgreSQL holds the counts that must join; MongoDB holds the per-row rejection detail whose shape varies by source file.';

CREATE TABLE sales_transaction (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_system           varchar(40) NOT NULL,
    external_transaction_id varchar(80),
    ingestion_run_id        bigint      REFERENCES ingestion_run(id),
    customer_id             bigint      REFERENCES customer(id),
    store_id                bigint      REFERENCES store(id),
    channel_code            varchar(30) NOT NULL REFERENCES sales_channel(code),
    transaction_type        varchar(10) NOT NULL DEFAULT 'sale',
    original_transaction_id bigint      REFERENCES sales_transaction(id),
    occurred_at             timestamptz NOT NULL,
    currency_code           char(3)     NOT NULL DEFAULT 'MXN',
    gross_amount            numeric(14,2) NOT NULL,
    discount_amount         numeric(14,2) NOT NULL DEFAULT 0,
    tax_amount              numeric(14,2) NOT NULL DEFAULT 0,
    net_amount              numeric(14,2) NOT NULL,
    line_count              smallint    NOT NULL DEFAULT 0,
    status                  varchar(20) NOT NULL DEFAULT 'settled',
    created_at              timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT ck_sales_transaction_type CHECK (transaction_type IN ('sale','return')),
    CONSTRAINT ck_sales_transaction_status CHECK (status IN ('settled','voided')),
    CONSTRAINT ck_sales_transaction_original
        CHECK (transaction_type = 'return' OR original_transaction_id IS NULL),
    CONSTRAINT ck_sales_transaction_sign CHECK (
        (transaction_type = 'sale'   AND net_amount >= 0) OR
        (transaction_type = 'return' AND net_amount <= 0))
);
COMMENT ON TABLE sales_transaction IS
'Header. Returns are first-class rows with negative amounts, not a flag: public retail datasets encode credit notes as negative-quantity documents, and treating them as sales inflates Monetary and makes "decreasing spend" unmeasurable.';
COMMENT ON CONSTRAINT ck_sales_transaction_original ON sales_transaction IS
'A return may reference its original document but is not required to: historical imports routinely contain credit notes whose original falls outside the imported window.';
CREATE UNIQUE INDEX ux_sales_transaction_external
    ON sales_transaction (source_system, external_transaction_id)
    WHERE external_transaction_id IS NOT NULL;
CREATE INDEX ix_sales_transaction_customer_time
    ON sales_transaction (customer_id, occurred_at DESC) WHERE status = 'settled';
CREATE INDEX ix_sales_transaction_occurred_at ON sales_transaction (occurred_at);
CREATE INDEX ix_sales_transaction_store ON sales_transaction (store_id);
CREATE INDEX ix_sales_transaction_ingestion_run ON sales_transaction (ingestion_run_id);

CREATE OR REPLACE FUNCTION fn_sales_transaction_channel_store() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    v_requires_store boolean;
BEGIN
    SELECT requires_store INTO v_requires_store
      FROM sales_channel WHERE code = NEW.channel_code;
    IF v_requires_store AND NEW.store_id IS NULL THEN
        RAISE EXCEPTION 'channel % requires store_id', NEW.channel_code;
    END IF;
    RETURN NEW;
END;
$$;
CREATE TRIGGER trg_sales_transaction_channel_store
    BEFORE INSERT OR UPDATE ON sales_transaction
    FOR EACH ROW EXECUTE FUNCTION fn_sales_transaction_channel_store();

-- Bulk load note (S0-08): this is a row trigger and costs a lookup per row.
-- The seed loader must COPY into a staging table, validate the channel/store
-- rule in one set-based statement, then insert with the trigger disabled:
--   ALTER TABLE sales_transaction DISABLE TRIGGER trg_sales_transaction_channel_store;
-- Re-enable before the run completes. Never disable it for interactive writes.

CREATE TABLE sales_transaction_line (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sales_transaction_id bigint        NOT NULL REFERENCES sales_transaction(id) ON DELETE CASCADE,
    line_number          smallint      NOT NULL,
    product_id           bigint        NOT NULL REFERENCES product(id),
    category_id          bigint        NOT NULL REFERENCES category(id),
    quantity             numeric(12,3) NOT NULL,
    unit_price           numeric(14,2) NOT NULL,
    discount_amount      numeric(14,2) NOT NULL DEFAULT 0,
    net_amount           numeric(14,2) GENERATED ALWAYS AS
        (round(quantity * unit_price - discount_amount, 2)) STORED,
    CONSTRAINT ux_sales_transaction_line_number UNIQUE (sales_transaction_id, line_number),
    CONSTRAINT ck_sales_transaction_line_quantity CHECK (quantity <> 0),
    CONSTRAINT ck_sales_transaction_line_price CHECK (unit_price >= 0)
);
COMMENT ON COLUMN sales_transaction_line.category_id IS
'Category at the time of sale, copied deliberately. product.category_id is mutable master data; if category-mix features read it live, re-categorizing one product silently rewrites the feature history of every past run and every stored RFM snapshot becomes irreproducible.';
CREATE INDEX ix_sales_transaction_line_product  ON sales_transaction_line (product_id);
CREATE INDEX ix_sales_transaction_line_category ON sales_transaction_line (category_id);

CREATE TABLE inventory_availability (
    id                 bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id         bigint        NOT NULL REFERENCES product(id),
    store_id           bigint        REFERENCES store(id),
    channel_code       varchar(30)   REFERENCES sales_channel(code),
    on_hand_quantity   numeric(12,3) NOT NULL DEFAULT 0,
    reserved_quantity  numeric(12,3) NOT NULL DEFAULT 0,
    available_quantity numeric(12,3) GENERATED ALWAYS AS
        (on_hand_quantity - reserved_quantity) STORED,
    reorder_point      numeric(12,3),
    as_of              timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ck_inventory_availability_nonneg
        CHECK (on_hand_quantity >= 0 AND reserved_quantity >= 0)
);
COMMENT ON TABLE inventory_availability IS
'Current state only. Availability history is telemetry and lives in MongoDB; versioning it in PostgreSQL would add a third versioned entity for no M1 requirement.';
CREATE UNIQUE INDEX ux_inventory_availability_scope
    ON inventory_availability (product_id, store_id, channel_code) NULLS NOT DISTINCT;

-- =============================================================================
-- 6. ANALYTICS RUNS
-- rfm_run is the parent of segmentation_model_run, not the reverse.
-- =============================================================================

CREATE TABLE rfm_run (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status               varchar(20)  NOT NULL DEFAULT 'running',
    analysis_window_start date        NOT NULL,
    analysis_window_end   date        NOT NULL,
    reference_date        date        NOT NULL,
    quintile_strategy    varchar(30)  NOT NULL DEFAULT 'ntile_equal_count',
    customer_scope       varchar(30)  NOT NULL DEFAULT 'consented_active',
    include_returns      boolean      NOT NULL DEFAULT true,
    parameters           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    code_version         varchar(40)  NOT NULL,
    triggered_by_user_id bigint       REFERENCES user_account(id),
    correlation_id       uuid         NOT NULL,
    started_at           timestamptz  NOT NULL DEFAULT now(),
    completed_at         timestamptz,
    customer_count       integer,
    transaction_count    integer,
    CONSTRAINT ck_rfm_run_status CHECK (status IN ('running','succeeded','failed')),
    CONSTRAINT ck_rfm_run_window CHECK (analysis_window_end > analysis_window_start),
    CONSTRAINT ck_rfm_run_reference CHECK (reference_date >= analysis_window_end),
    CONSTRAINT ck_rfm_run_scope CHECK (customer_scope IN ('all','consented_active','cohort'))
);
COMMENT ON TABLE rfm_run IS
'Feature computation, separate from clustering. Separation exists so that (a) two clusterings with different k can be compared over one identical feature set, which is the only valid comparison, (b) drift detection and incremental segmentation in M2 can consume features without triggering a clustering, and (c) recomputing features for every candidate k is not forced.';
COMMENT ON COLUMN rfm_run.analysis_window_end IS
'Exclusive. Windows are half-open [start, end) so that consecutive windows neither overlap nor leave gaps.';

CREATE TABLE customer_rfm_snapshot (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rfm_run_id              bigint        NOT NULL REFERENCES rfm_run(id) ON DELETE CASCADE,
    customer_id             bigint        NOT NULL REFERENCES customer(id),
    -- RFM core
    recency_days            integer       NOT NULL,
    frequency               integer       NOT NULL,
    monetary                numeric(14,2) NOT NULL,
    r_score                 smallint      NOT NULL,
    f_score                 smallint      NOT NULL,
    m_score                 smallint      NOT NULL,
    rfm_cell                char(3)       NOT NULL,
    -- behavioural features: one column per migration dimension named in the
    -- problem statement, so a migration report can say WHY a customer moved
    avg_order_value         numeric(14,2),
    avg_interpurchase_days  numeric(10,2),
    distinct_category_count smallint,
    top_category_id         bigint        REFERENCES category(id),
    top_category_share      numeric(6,4),
    distinct_store_count    smallint,
    dominant_store_id       bigint        REFERENCES store(id),
    dominant_channel_code   varchar(30)   REFERENCES sales_channel(code),
    digital_share           numeric(6,4),
    promo_response_rate     numeric(6,4),
    return_count            integer       NOT NULL DEFAULT 0,
    return_amount           numeric(14,2) NOT NULL DEFAULT 0,
    tenure_days             integer,
    first_purchase_at       timestamptz,
    last_purchase_at        timestamptz,
    computed_at             timestamptz   NOT NULL DEFAULT now(),
    CONSTRAINT ux_customer_rfm_snapshot UNIQUE (rfm_run_id, customer_id),
    CONSTRAINT ck_customer_rfm_snapshot_scores CHECK (
        r_score BETWEEN 1 AND 5 AND f_score BETWEEN 1 AND 5 AND m_score BETWEEN 1 AND 5),
    CONSTRAINT ck_customer_rfm_snapshot_recency CHECK (recency_days >= 0),
    CONSTRAINT ck_customer_rfm_snapshot_frequency CHECK (frequency >= 0),
    CONSTRAINT ck_customer_rfm_snapshot_shares CHECK (
        (top_category_share IS NULL OR top_category_share BETWEEN 0 AND 1) AND
        (digital_share      IS NULL OR digital_share      BETWEEN 0 AND 1))
);
COMMENT ON TABLE customer_rfm_snapshot IS
'Immutable per (run, customer). Never updated. The name is narrower than the content: it carries the full feature vector, because a segment migration report that cannot say whether the cause was category shift, channel switch, store change, frequency change or spend change does not answer the question the project was set.';
CREATE INDEX ix_customer_rfm_snapshot_customer
    ON customer_rfm_snapshot (customer_id, rfm_run_id);
CREATE INDEX ix_customer_rfm_snapshot_cell ON customer_rfm_snapshot (rfm_run_id, rfm_cell);

CREATE TABLE segmentation_model_run (
    id                   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rfm_run_id           bigint       NOT NULL REFERENCES rfm_run(id),
    algorithm            varchar(40)  NOT NULL DEFAULT 'kmeans',
    algorithm_version    varchar(20)  NOT NULL,
    library_version      varchar(40)  NOT NULL,
    code_version         varchar(40)  NOT NULL,
    k                    smallint     NOT NULL,
    random_seed          integer      NOT NULL,
    feature_set_version  varchar(20)  NOT NULL,
    scaler_kind          varchar(30)  NOT NULL,
    scaler_state         jsonb        NOT NULL,
    labelling_strategy   varchar(40)  NOT NULL DEFAULT 'centroid_value_rank_v1',
    parameters           jsonb        NOT NULL DEFAULT '{}'::jsonb,
    model_artifact_uri   text,
    silhouette           numeric(6,4),
    davies_bouldin       numeric(8,4),
    inertia              numeric(18,4),
    purpose              varchar(20)  NOT NULL DEFAULT 'production',
    status               varchar(20)  NOT NULL DEFAULT 'running',
    triggered_by_user_id bigint       REFERENCES user_account(id),
    correlation_id       uuid         NOT NULL,
    started_at           timestamptz  NOT NULL DEFAULT now(),
    completed_at         timestamptz,
    promoted_at          timestamptz,
    superseded_by_run_id bigint       REFERENCES segmentation_model_run(id),
    notes                text,
    CONSTRAINT ck_segmentation_model_run_k CHECK (k BETWEEN 2 AND 20),
    CONSTRAINT ck_segmentation_model_run_purpose
        CHECK (purpose IN ('production','candidate','experiment')),
    CONSTRAINT ck_segmentation_model_run_status
        CHECK (status IN ('running','succeeded','failed','discarded')),
    CONSTRAINT ck_segmentation_model_run_promotion
        CHECK (promoted_at IS NULL OR purpose = 'production')
);
COMMENT ON COLUMN segmentation_model_run.scaler_state IS
'Fitted scaler parameters (means, scales, feature order). Without persistence the run is not reproducible and no future point can be scored against this model.';
COMMENT ON COLUMN segmentation_model_run.purpose IS
'production runs write authoritative assignments. candidate and experiment runs write non-authoritative assignments that coexist with production, which is what makes model comparison (E-27) possible.';
COMMENT ON COLUMN segmentation_model_run.labelling_strategy IS
'K-means cluster indices are arbitrary and unstable between runs. Mapping cluster to segment_label must be a deterministic rule over centroid position, recorded here, or migration detection reports noise as behaviour change.';
CREATE INDEX ix_segmentation_model_run_rfm ON segmentation_model_run (rfm_run_id);
CREATE INDEX ix_segmentation_model_run_completed
    ON segmentation_model_run (completed_at DESC)
    WHERE status = 'succeeded' AND purpose = 'production';

CREATE TABLE segment (
    id                      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_run_id            bigint        NOT NULL
        REFERENCES segmentation_model_run(id) ON DELETE CASCADE,
    cluster_index           smallint      NOT NULL,
    label_code              varchar(40)   REFERENCES segment_label(code),
    centroid_scaled         jsonb         NOT NULL,
    centroid_original_scale jsonb,
    member_count            integer       NOT NULL DEFAULT 0,
    avg_recency_days        numeric(10,2),
    avg_frequency           numeric(10,2),
    avg_monetary            numeric(14,2),
    revenue_share           numeric(6,4),
    description             text,
    CONSTRAINT ux_segment_run_cluster UNIQUE (model_run_id, cluster_index),
    CONSTRAINT ck_segment_member_count CHECK (member_count >= 0)
);
COMMENT ON TABLE segment IS
'Segment definitions belong to one run. Cross-run identity is segment_label.code, never segment.id.';
CREATE UNIQUE INDEX ux_segment_run_label
    ON segment (model_run_id, label_code) WHERE label_code IS NOT NULL;

CREATE TABLE customer_segment_assignment (
    id                          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id                 bigint        NOT NULL REFERENCES customer(id),
    segment_id                  bigint        NOT NULL REFERENCES segment(id),
    model_run_id                bigint        NOT NULL REFERENCES segmentation_model_run(id),
    rfm_snapshot_id             bigint        NOT NULL REFERENCES customer_rfm_snapshot(id),
    distance_to_centroid        numeric(12,6),
    assignment_confidence       numeric(6,4),
    is_authoritative            boolean       NOT NULL DEFAULT true,
    -- valid time: when the customer belonged to this segment
    valid_from                  timestamptz   NOT NULL,
    valid_to                    timestamptz,
    -- decision time: when the platform believed it
    recorded_at                 timestamptz   NOT NULL DEFAULT now(),
    superseded_at               timestamptz,
    superseded_by_assignment_id bigint        REFERENCES customer_segment_assignment(id),
    supersede_reason            varchar(40),
    closed_by_run_id            bigint        REFERENCES segmentation_model_run(id),
    CONSTRAINT ck_customer_segment_assignment_validity
        CHECK (valid_to IS NULL OR valid_to > valid_from),
    CONSTRAINT ck_customer_segment_assignment_supersede
        CHECK ((superseded_at IS NULL) = (superseded_by_assignment_id IS NULL)),
    CONSTRAINT ck_customer_segment_assignment_confidence
        CHECK (assignment_confidence IS NULL OR assignment_confidence BETWEEN 0 AND 1),
    CONSTRAINT ex_customer_segment_assignment_no_overlap EXCLUDE USING gist (
        customer_id WITH =,
        tstzrange(valid_from, valid_to) WITH &&
    ) WHERE (is_authoritative AND superseded_at IS NULL)
      DEFERRABLE INITIALLY IMMEDIATE
);
COMMENT ON TABLE customer_segment_assignment IS
'Bi-temporal. Valid time (valid_from, valid_to) answers "which segment was the customer in on date D". Decision time (recorded_at, superseded_at) answers "what did the platform believe on date T". Both axes are needed: correcting a faulty run must not be indistinguishable from the customer actually changing behaviour.';
COMMENT ON CONSTRAINT ex_customer_segment_assignment_no_overlap ON customer_segment_assignment IS
'Replaces the partial unique index originally specified in 03-sprint-00-backlog.md. Strictly stronger: it rejects any overlapping validity interval, not merely a second open one, so a mutable-assignment implementation fails at the database level rather than producing plausible-looking history. Restricted to authoritative, non-superseded rows so candidate runs may coexist.';
COMMENT ON COLUMN customer_segment_assignment.valid_to IS
'Closing a prior assignment and opening the next one must use the SAME instant, taken from segmentation_model_run.completed_at. tstzrange is half-open, so [t1,t2) and [t2,NULL) neither overlap nor leave a gap. Any other pairing produces either a rejected insert or an unrepresented interval.';
-- The constraint is DEFERRABLE INITIALLY IMMEDIATE, verified behaviour:
--   default            -> an overlapping row fails at statement time (fail fast)
--   SET CONSTRAINTS ex_customer_segment_assignment_no_overlap DEFERRED
--                      -> the pipeline may insert all new assignments first and
--                         close the previous ones afterwards, in either order
--   a genuine overlap under DEFERRED still fails at COMMIT
-- Use the DEFERRED form only inside the segmentation run transaction.
CREATE INDEX ix_customer_segment_assignment_customer_valid
    ON customer_segment_assignment (customer_id, valid_from DESC);
CREATE INDEX ix_customer_segment_assignment_run
    ON customer_segment_assignment (model_run_id);
CREATE INDEX ix_customer_segment_assignment_open
    ON customer_segment_assignment (segment_id)
    WHERE valid_to IS NULL AND superseded_at IS NULL AND is_authoritative;

-- =============================================================================
-- 7. AUDIT AND CONFIGURATION
-- =============================================================================

CREATE TABLE audit_log (
    id                       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at              timestamptz   NOT NULL DEFAULT now(),
    actor_user_id            bigint        REFERENCES user_account(id),
    actor_email              varchar(255),
    actor_role_codes         varchar(50)[],
    on_behalf_of_customer_id bigint        REFERENCES customer(id),
    action                   varchar(60)   NOT NULL,
    entity_type              varchar(60)   NOT NULL,
    entity_id                varchar(64),
    before_data              jsonb,
    after_data               jsonb,
    changed_fields           varchar(60)[],
    source_component         varchar(30)   NOT NULL,
    client_ip                inet,
    user_agent               text,
    request_path             text,
    http_status              smallint,
    correlation_id           uuid          NOT NULL,
    CONSTRAINT ck_audit_log_component CHECK (source_component IN
        ('web','microservice','mobile','desktop','job','seed')),
    CONSTRAINT ck_audit_log_payload CHECK (
        action <> 'update' OR (before_data IS NOT NULL AND after_data IS NOT NULL))
);
COMMENT ON TABLE audit_log IS
'Append-only. entity_id is varchar and carries no foreign key on purpose: a real FK would block deletion of any audited row and would need one nullable column per entity type.';
COMMENT ON COLUMN audit_log.actor_email IS
'Snapshot of the actor identity at the time of the action. A renamed or deleted account must not rewrite history.';
CREATE INDEX ix_audit_log_occurred_at  ON audit_log (occurred_at DESC);
CREATE INDEX ix_audit_log_entity       ON audit_log (entity_type, entity_id, occurred_at DESC);
CREATE INDEX ix_audit_log_actor        ON audit_log (actor_user_id, occurred_at DESC);
CREATE INDEX ix_audit_log_correlation  ON audit_log (correlation_id);
CREATE INDEX ix_audit_log_action       ON audit_log (action, occurred_at DESC);

CREATE TRIGGER trg_audit_log_append_only
    BEFORE UPDATE OR DELETE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION fn_reject_write();

CREATE TRIGGER trg_audit_log_no_truncate
    BEFORE TRUNCATE ON audit_log
    FOR EACH STATEMENT EXECUTE FUNCTION fn_reject_write();

CREATE TABLE system_setting (
    key                varchar(80)  PRIMARY KEY,
    value_text         text,
    value_type         varchar(20)  NOT NULL,
    description        text         NOT NULL,
    is_sensitive       boolean      NOT NULL DEFAULT false,
    updated_by_user_id bigint       REFERENCES user_account(id),
    updated_at         timestamptz  NOT NULL DEFAULT now(),
    CONSTRAINT ck_system_setting_type
        CHECK (value_type IN ('string','integer','decimal','boolean','json'))
);
COMMENT ON COLUMN system_setting.is_sensitive IS
'Mask the value in the administration UI and in audit_log payloads. This is not a secret store: credentials, keys and connection strings live in Secret Manager and are injected as environment variables, never persisted here.';

INSERT INTO system_setting (key, value_text, value_type, description) VALUES
    ('rfm.default_window_months',   '12',   'integer', 'Default analysis window length for a new RFM run.'),
    ('rfm.quintile_strategy',       'ntile_equal_count', 'string', 'Quintile boundary strategy.'),
    ('segmentation.default_k',      '5',    'integer', 'Default cluster count.'),
    ('segmentation.random_seed',    '42',   'integer', 'Default seed, for reproducibility.'),
    ('auth.access_token_ttl_seconds',  '900',    'integer', 'Access token lifetime. See ADR-001.'),
    ('auth.refresh_token_ttl_seconds', '604800', 'integer', 'Refresh token lifetime. See ADR-001.'),
    ('auth.max_failed_logins',      '5',    'integer', 'Failed attempts before lockout.');

-- =============================================================================
-- 8. VIEWS
-- The migration and point-in-time logic lives here, under migration control,
-- so that the web system, the microservices and the desktop client cannot
-- drift into three different definitions of "migration".
-- =============================================================================

CREATE VIEW v_customer_segment_current AS
SELECT a.customer_id,
       a.segment_id,
       s.label_code,
       s.cluster_index,
       a.model_run_id,
       a.valid_from,
       a.distance_to_centroid,
       a.assignment_confidence
FROM   customer_segment_assignment a
JOIN   segment s ON s.id = a.segment_id
WHERE  a.valid_to IS NULL
  AND  a.superseded_at IS NULL
  AND  a.is_authoritative;

CREATE VIEW v_segment_migration AS
WITH history AS (
    SELECT a.customer_id,
           a.id                                        AS assignment_id,
           a.model_run_id,
           a.segment_id,
           a.valid_from,
           s.label_code,
           sl.value_rank,
           LAG(a.model_run_id) OVER w AS from_model_run_id,
           LAG(a.segment_id)   OVER w AS from_segment_id,
           LAG(s.label_code)   OVER w AS from_label_code,
           LAG(sl.value_rank)  OVER w AS from_value_rank,
           LAG(a.valid_from)   OVER w AS from_valid_from
    FROM   customer_segment_assignment a
    JOIN   segment s        ON s.id  = a.segment_id
    LEFT   JOIN segment_label sl ON sl.code = s.label_code
    WHERE  a.is_authoritative
      AND  a.superseded_at IS NULL
    WINDOW w AS (PARTITION BY a.customer_id ORDER BY a.valid_from, a.id)
)
SELECT customer_id,
       from_model_run_id,
       model_run_id      AS to_model_run_id,
       from_segment_id,
       segment_id        AS to_segment_id,
       from_label_code,
       label_code        AS to_label_code,
       from_valid_from,
       valid_from        AS migrated_at,
       CASE
         WHEN from_value_rank IS NULL OR value_rank IS NULL THEN 'unclassified'
         WHEN value_rank > from_value_rank THEN 'upgrade'
         WHEN value_rank < from_value_rank THEN 'downgrade'
         ELSE 'lateral'
       END               AS direction
FROM   history
WHERE  from_label_code IS NOT NULL
  AND  from_label_code IS DISTINCT FROM label_code;

COMMENT ON VIEW v_segment_migration IS
'Migration is a change of segment_label between consecutive authoritative assignments. It is NOT a change of segment_id: segments are run-scoped, so segment_id changes for every customer on every run and comparing it would report 100 percent migration every time.';

CREATE VIEW v_customer_behavior_delta AS
WITH ordered AS (
    SELECT sn.customer_id,
           r.id                        AS rfm_run_id,
           r.analysis_window_end,
           sn.frequency, sn.monetary, sn.recency_days,
           sn.top_category_id, sn.dominant_channel_code, sn.dominant_store_id,
           LAG(r.id)                    OVER w AS from_rfm_run_id,
           LAG(sn.frequency)            OVER w AS from_frequency,
           LAG(sn.monetary)             OVER w AS from_monetary,
           LAG(sn.recency_days)         OVER w AS from_recency_days,
           LAG(sn.top_category_id)      OVER w AS from_top_category_id,
           LAG(sn.dominant_channel_code)OVER w AS from_dominant_channel_code,
           LAG(sn.dominant_store_id)    OVER w AS from_dominant_store_id
    FROM   customer_rfm_snapshot sn
    JOIN   rfm_run r ON r.id = sn.rfm_run_id AND r.status = 'succeeded'
    WINDOW w AS (PARTITION BY sn.customer_id ORDER BY r.analysis_window_end, r.id)
)
SELECT customer_id, from_rfm_run_id, rfm_run_id, analysis_window_end,
       from_frequency, frequency,
       (frequency - from_frequency)                       AS frequency_delta,
       from_monetary, monetary,
       (monetary  - from_monetary)                        AS monetary_delta,
       (from_top_category_id      IS DISTINCT FROM top_category_id)       AS category_shifted,
       (from_dominant_channel_code IS DISTINCT FROM dominant_channel_code) AS channel_switched,
       (from_dominant_store_id    IS DISTINCT FROM dominant_store_id)      AS store_switched
FROM   ordered
WHERE  from_rfm_run_id IS NOT NULL;

COMMENT ON VIEW v_customer_behavior_delta IS
'Explains a migration. Covers the six behaviour changes named in the problem statement: purchase frequency, category, promotion response, store, spend level and channel.';


-- =============================================================================
-- 9. Runtime role — executed once per environment, OUTSIDE Alembic
-- The application must not connect as the schema owner, or the REVOKE below
-- has no effect and audit_log is only append-only by convention.
-- =============================================================================
-- CREATE ROLE app_runtime LOGIN PASSWORD :'runtime_password';
-- GRANT USAGE ON SCHEMA public TO app_runtime;
-- GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES    IN SCHEMA public TO app_runtime;
-- GRANT USAGE, SELECT                  ON ALL SEQUENCES IN SCHEMA public TO app_runtime;
-- REVOKE UPDATE, DELETE, TRUNCATE      ON audit_log FROM app_runtime;
-- ALTER DEFAULT PRIVILEGES IN SCHEMA public
--     GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_runtime;
--
-- .env therefore carries two DSNs: DATABASE_URL (app_runtime) and
-- DATABASE_MIGRATION_URL (owner). This is a hard dependency on S0-02 and S0-03.

"""

_TABLES = [
    "sales_channel",
    "segment_label",
    "consent_purpose",
    "privacy_notice_version",
    "store",
    "category",
    "customer",
    "user_account",
    "role",
    "permission",
    "role_permission",
    "user_role",
    "password_reset_token",
    "file_object",
    "product",
    "consent_record",
    "ingestion_run",
    "sales_transaction",
    "sales_transaction_line",
    "inventory_availability",
    "rfm_run",
    "customer_rfm_snapshot",
    "segmentation_model_run",
    "segment",
    "customer_segment_assignment",
    "audit_log",
    "system_setting",
]

_VIEWS = [
    "v_customer_segment_current",
    "v_segment_migration",
    "v_customer_behavior_delta",
]

_FUNCTIONS = [
    "fn_set_updated_at()",
    "fn_reject_write()",
    "fn_user_role_scope_matches()",
    "fn_sales_transaction_channel_store()",
]


def upgrade() -> None:
    """Apply the frozen M1 schema.

    Executed on a raw DBAPI cursor with **no parameters**, which is the only way
    to run this script unaltered. Two layers would otherwise rewrite it:

    * ``op.execute`` wraps a string in SQLAlchemy's ``text()``, which reads
      ``:name`` as a bind parameter. The DDL is full of ``::jsonb`` casts.
    * ``exec_driver_sql`` reaches the driver but still passes an empty parameter
      set, and psycopg then parses ``%`` as a placeholder and fails with
      ``only '%s', '%b', '%t' are allowed as placeholders``. The DDL contains
      four ``%`` signs, all of them ``RAISE EXCEPTION`` format specifiers inside
      PL/pgSQL trigger functions -- and those exact error strings are what
      CHECKS 9, 11, 14 and 15 of verify_m1_schema.sql assert on. Escaping them
      to ``%%`` would change the messages the schema is verified against.

    psycopg only parses placeholders when parameters are supplied. Passing none
    sends the script through untouched.

    The cursor comes from the connection Alembic is already using, so this runs
    inside the revision's transaction and rolls back with it.
    """
    dbapi_connection = op.get_bind().connection
    with dbapi_connection.cursor() as cur:
        cur.execute(SCHEMA_SQL)


def downgrade() -> None:
    """Return the database to empty.

    Tables are dropped with CASCADE so declaration order does not matter and the
    three views fall with them; the views are dropped first anyway so the intent
    is explicit. The two extensions are dropped last because the exclusion
    constraints depend on btree_gist.
    """
    bind = op.get_bind()
    for view in _VIEWS:
        bind.exec_driver_sql(f"DROP VIEW IF EXISTS {view} CASCADE;")
    for table in _TABLES:
        bind.exec_driver_sql(f"DROP TABLE IF EXISTS {table} CASCADE;")
    for fn in _FUNCTIONS:
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {fn} CASCADE;")
    bind.exec_driver_sql("DROP EXTENSION IF EXISTS pg_trgm;")
    bind.exec_driver_sql("DROP EXTENSION IF EXISTS btree_gist;")
