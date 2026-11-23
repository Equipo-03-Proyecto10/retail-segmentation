//! What F22-04's stand-up criteria ask of the directory (ADR-0034, ADR-0040,
//! ADR-0045, AGENTS.md), and the boundary the database holds (ADR-0044,
//! ADR-0046): the role reaches the eight views it reads and nothing else.

mod support;

use std::fs;
use std::path::Path;

use support::{connect, owner_url};

fn rust_files(dir: &Path, found: &mut Vec<std::path::PathBuf>) {
    for entry in fs::read_dir(dir).unwrap() {
        let path = entry.unwrap().path();
        if path.is_dir() {
            rust_files(&path, found);
        } else if path.extension().is_some_and(|ext| ext == "rs") {
            found.push(path);
        }
    }
}

#[test]
fn kit_is_pinned_by_tag_and_the_crate_has_no_workspace_or_path_dependency() {
    let manifest = fs::read_to_string("Cargo.toml").unwrap();
    assert!(manifest.contains("tag = \"kit-rs/v0.1.5\""));
    assert!(!manifest.contains("[workspace]"));
    assert!(!manifest.contains("path ="));
}

#[test]
fn the_directory_holds_what_a_stood_up_service_holds() {
    for file in [
        "Cargo.toml",
        "Cargo.lock",
        "Dockerfile",
        "VERSION",
        "compose.yaml",
        "nginx/analytics-kpis.conf",
        "redis/analytics-kpis.acl",
        "src",
        "tests",
    ] {
        assert!(Path::new(file).exists(), "{file}");
    }
    assert!(!Path::new("uv.lock").exists(), "one lock file: Cargo.lock");
    let version = fs::read_to_string("VERSION").unwrap();
    let parts: Vec<&str> = version.trim().split('.').collect();
    assert_eq!(parts.len(), 3, "semver VERSION");
    assert!(parts.iter().all(|part| part.parse::<u32>().is_ok()));
}

#[test]
fn no_sql_is_built_by_format_or_by_concatenation() {
    let mut files = Vec::new();
    rust_files(Path::new("src"), &mut files);
    assert!(!files.is_empty(), "src/ holds no Rust file");
    for path in files {
        let source = fs::read_to_string(&path).unwrap();
        for line in source.lines() {
            for builder in ["format!(", "push_str("] {
                if let Some(at) = line.find(builder) {
                    for keyword in ["SELECT", "INSERT", "UPDATE", "DELETE", "FROM", "WHERE"] {
                        assert!(
                            !line[at..].contains(keyword),
                            "{}: SQL built by {builder}: {line}",
                            path.display()
                        );
                    }
                }
            }
        }
    }
}

#[test]
fn the_service_writes_nothing_and_names_no_table_of_its_own() {
    let mut files = Vec::new();
    rust_files(Path::new("src"), &mut files);
    for path in files {
        let source = fs::read_to_string(&path).unwrap().to_uppercase();
        for statement in [
            "INSERT INTO",
            "UPDATE ",
            "DELETE FROM",
            "CREATE TABLE",
            "ALTER TABLE",
            "LISTEN ",
            "NOTIFY ",
        ] {
            // A word of prose such as "UPDATE" in a doc comment is not SQL:
            // a statement is a string literal, and no literal holds these.
            let in_literal = source
                .lines()
                .any(|line| line.contains('"') && line.contains(statement));
            assert!(!in_literal, "{}: {statement}", path.display());
        }
    }
}

#[test]
fn the_image_builds_from_its_own_directory_only() {
    let dockerfile = fs::read_to_string("Dockerfile").unwrap();
    for line in dockerfile.lines() {
        let line = line.trim_start();
        if line.starts_with("COPY") || line.starts_with("ADD") {
            assert!(!line.contains("../") && !line.contains("web/"), "{line}");
        }
    }
}

#[test]
fn the_compose_project_is_alone_hardened_and_within_its_budget() {
    let compose = fs::read_to_string("compose.yaml").unwrap();
    for wanted in [
        "name: mosaiq-analytics-kpis",
        "image: ghcr.io/equipo-03-proyecto10/mosaiq-analytics-kpis:${IMAGE_TAG}",
        "\"127.0.0.1:5022:5022\"",
        // ADR-0059: 64 MB for the service.
        "mem_limit: 64m",
        "cpus: 0.25",
        "user: \"10001:10001\"",
        "cap_drop: [ALL]",
        "no-new-privileges:true",
        "read_only: true",
        "networks: [mosaiq]",
        "external: true",
        "/analytics-kpis.env",
    ] {
        assert!(compose.contains(wanted), "{wanted}");
    }
    assert!(!compose
        .lines()
        .any(|line| line.trim_start().starts_with("depends_on:")));
    let env = fs::read_to_string("../../docker/distributed/svc/analytics-kpis.env").unwrap();
    assert!(env.contains("DATABASE_URL=postgresql://svc_kpis@/retail?host=/run/postgresql"));
    assert!(env.contains("MOSAIQ_SERVICE_NAME=analytics-kpis"));
}

#[test]
fn nginx_forwards_the_real_client_and_caps_the_body() {
    let nginx = fs::read_to_string("nginx/analytics-kpis.conf").unwrap();
    for wanted in [
        "location /api/analytics-kpis/ {",
        "proxy_pass http://127.0.0.1:5022/;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $remote_addr;",
        "client_max_body_size 64k;",
    ] {
        assert!(nginx.contains(wanted), "{wanted}");
    }
}

#[tokio::test]
async fn the_role_reads_eight_published_views_and_no_base_table() {
    let Some(owner_url) = owner_url() else { return };
    let owner = connect(&owner_url).await;

    // Its tables-and-views privileges are exactly the eight views of grants.csv.
    let granted: Vec<String> = owner
        .query(
            "SELECT table_schema || '.' || table_name || ':' || privilege_type \
             FROM information_schema.role_table_grants WHERE grantee = $1 \
             ORDER BY 1",
            &[&"svc_kpis"],
        )
        .await
        .unwrap()
        .iter()
        .map(|row| row.get(0))
        .collect();
    assert_eq!(
        granted,
        [
            "api_experiments.campaign_effect_v1:SELECT",
            "api_models.run_version_v1:SELECT",
            "api_products.product_ref_v1:SELECT",
            "api_sales.net_purchase_line_v1:SELECT",
            "api_segments.assignment_change_v1:SELECT",
            "api_segments.current_label_v1:SELECT",
            "api_stock.stock_level_v1:SELECT",
            "api_users.store_assignment_v1:SELECT",
        ]
    );

    // No privilege of any kind on a table of `public`, owned by another boundary
    // or by the web (ADR-0046), and none on a relation of a schema it does not
    // read.
    let reached: i64 = owner
        .query_one(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace \
             WHERE c.relkind IN ('r', 'v', 'm', 'p', 'f') \
               AND n.nspname NOT IN ('pg_catalog', 'information_schema') \
               AND has_table_privilege('svc_kpis', c.oid, \
                   'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER') \
               AND (n.nspname || '.' || c.relname) NOT IN ($1, $2, $3, $4, $5, $6, $7, $8)",
            &[
                &"api_sales.net_purchase_line_v1",
                &"api_segments.current_label_v1",
                &"api_users.store_assignment_v1",
                &"api_stock.stock_level_v1",
                &"api_products.product_ref_v1",
                &"api_segments.assignment_change_v1",
                &"api_models.run_version_v1",
                &"api_experiments.campaign_effect_v1",
            ],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(reached, 0);

    // It owns schemas of its own (ADR-0059) and creates nothing in them.
    for schema in ["kpis", "api_kpis"] {
        let exists: bool = owner
            .query_one("SELECT to_regnamespace($1) IS NOT NULL", &[&schema])
            .await
            .unwrap()
            .get(0);
        assert!(exists, "{schema}");
        let can_create: bool = owner
            .query_one(
                "SELECT has_schema_privilege('svc_kpis', $1, 'CREATE')",
                &[&schema],
            )
            .await
            .unwrap()
            .get(0);
        assert!(!can_create, "{schema}");
    }
    let tables: i64 = owner
        .query_one(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace \
             WHERE n.nspname IN ('kpis', 'api_kpis') AND c.relkind IN ('r', 'p')",
            &[],
        )
        .await
        .unwrap()
        .get(0);
    assert_eq!(tables, 0, "it owns no table");

    // It cannot write, and cannot reach a neighbouring view it does not read.
    let service = connect(&support::service_url(&owner_url)).await;
    for statement in [
        "SELECT 1 FROM public.transaction LIMIT 1",
        "SELECT 1 FROM api_sales.purchase_line_v1 LIMIT 1",
        "SELECT 1 FROM api_stock.availability_v1 LIMIT 1",
        "SELECT 1 FROM api_customers.customer_ref_v1 LIMIT 1",
        "INSERT INTO public.audit_log (entity, entity_pk, action) VALUES ('x', 'y', 'INSERT')",
        "CREATE TABLE kpis.t (a integer)",
    ] {
        let refused = service.batch_execute(statement).await.unwrap_err();
        assert_eq!(
            refused.code().map(|code| code.code()),
            Some("42501"),
            "{statement}"
        );
    }
}
