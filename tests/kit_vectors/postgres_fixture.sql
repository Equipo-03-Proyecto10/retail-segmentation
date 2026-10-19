-- SETUP
CREATE ROLE definer_<suffix> NOLOGIN;
CREATE ROLE svc_<suffix> NOLOGIN;
CREATE SCHEMA <suffix>;
CREATE TABLE <suffix>.item (
    item_id integer PRIMARY KEY,
    label text NOT NULL
);

-- TEARDOWN
DROP SCHEMA IF EXISTS <suffix>_events CASCADE;
DROP SCHEMA IF EXISTS <suffix> CASCADE;
DROP ROLE IF EXISTS svc_<suffix>;
DROP ROLE IF EXISTS definer_<suffix>;
